"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Card, PageHeader, Skeleton } from "@/components/ui";
import {
  studentPortalApi,
  type StudentPortalAssignmentDetail,
} from "@/lib/api";

const questionTypeLabels: Record<string, string> = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  true_false: "判断题",
  fill_blank: "填空题",
  short_answer: "简答题",
  essay: "主观题",
  calculation: "计算题",
};

export default function StudentAssignmentPage() {
  const { releaseId } = useParams<{ releaseId: string }>();
  const [data, setData] = useState<StudentPortalAssignmentDetail>();
  const [error, setError] = useState("");
  useEffect(() => {
    studentPortalApi
      .assignment(releaseId)
      .then(setData)
      .catch(() => setError("成绩不存在或尚未向学生开放。"));
  }, [releaseId]);

  if (error)
    return <Card className="border-red-300 p-5 text-red-700">{error}</Card>;
  if (!data) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-6">
      <Link className="text-sm text-blue-700 hover:underline" href="/student">
        ← 返回我的作业
      </Link>
      <PageHeader
        title={data.assignment_title}
        description={`${data.class_name}${data.subject ? ` · ${data.subject}` : ""} · 正式成绩第 ${data.release_version} 版`}
      />
      <Card className="p-6">
        <div className="grid gap-5 sm:grid-cols-3">
          <div>
            <p className="text-sm text-slate-500">总分</p>
            <p className="mt-1 text-3xl font-bold">
              {formatScore(data.total_score)} / {formatScore(data.max_score)}
            </p>
          </div>
          <div>
            <p className="text-sm text-slate-500">得分率</p>
            <p className="mt-1 text-3xl font-bold">
              {(data.score_rate * 100).toFixed(1)}%
            </p>
          </div>
          <div className="flex items-end sm:justify-end">
            <a
              className="rounded-lg border px-4 py-2 font-medium text-blue-700 hover:bg-blue-50"
              href={studentPortalApi.reportUrl(data.release_id)}
            >
              下载个人报告
            </a>
          </div>
        </div>
      </Card>
      {data.versions.length > 1 && (
        <Card className="p-5">
          <h2 className="font-bold">成绩版本</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {data.versions.map((version) => (
              <Link
                key={version.release_id}
                href={`/student/${version.release_id}`}
                className={`rounded-lg border px-3 py-2 text-sm ${version.current ? "border-blue-500 bg-blue-50 font-semibold text-blue-700" : "hover:bg-slate-50"}`}
              >
                第 {version.version} 版{version.current ? "（当前）" : ""}
              </Link>
            ))}
          </div>
        </Card>
      )}
      <section className="space-y-3">
        <h2 className="text-lg font-bold">各题结果</h2>
        {data.questions.map((question) => (
          <Card className="p-5" key={question.question_id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-bold">第 {question.question_number} 题</h3>
                <p className="text-sm text-slate-500">
                  {questionTypeLabels[question.question_type] ??
                    question.question_type}
                </p>
              </div>
              <strong className="text-lg">
                {formatScore(question.score)} /{" "}
                {formatScore(question.max_score)}
              </strong>
            </div>
            {question.feedback && (
              <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm">
                <p className="font-medium">教师评语</p>
                <p className="mt-1 whitespace-pre-wrap text-slate-700">
                  {question.feedback}
                </p>
              </div>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              {question.error_type && (
                <span className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-800">
                  {question.error_type}
                </span>
              )}
              {question.knowledge_points.map((point) => (
                <span
                  className="rounded-full bg-blue-50 px-2.5 py-1 text-blue-700"
                  key={point.id}
                >
                  {point.name}
                </span>
              ))}
            </div>
          </Card>
        ))}
      </section>
    </div>
  );
}

function formatScore(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

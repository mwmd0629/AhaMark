"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
  StatCard,
} from "@/components/ui";
import { studentPortalApi, type StudentLearningAnalysis } from "@/lib/api";

export default function StudentLearningPage() {
  const [analysis, setAnalysis] = useState<StudentLearningAnalysis>();
  const [error, setError] = useState("");
  useEffect(() => {
    studentPortalApi
      .learningAnalysis()
      .then(setAnalysis)
      .catch(() => setError("学习分析加载失败，请稍后重试。"));
  }, []);
  if (!analysis) return <Skeleton className="h-72 w-full" />;
  return (
    <div className="space-y-6">
      <PageHeader
        title="学习分析"
        description="仅根据教师已正式发布的成绩快照汇总，不参与评分或改分。"
      />
      {error && (
        <Card className="border-red-300 p-4 text-red-700">{error}</Card>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="当前错题"
          value={String(analysis.wrong_question_count)}
          note="来自最新正式成绩"
        />
        <StatCard
          label="重点知识点"
          value={String(analysis.focus_knowledge_points.length)}
          note="按错题出现频次"
        />
        <StatCard
          label="本地学习助手"
          value={analysis.assistant_enabled ? "已启用" : "未启用"}
          note="默认关闭，仅提供建议"
        />
      </div>
      {!analysis.wrong_question_count ? (
        <EmptyState
          icon="analytics"
          title="暂无需要分析的错题"
          description="教师发布新的正式成绩后，这里会自动更新。"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="p-5">
            <h2 className="font-bold">重点知识点</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {analysis.focus_knowledge_points.map((item) => (
                <li
                  className="flex justify-between rounded-lg bg-slate-50 p-3"
                  key={item.name}
                >
                  <span>{item.name}</span>
                  <strong>{item.count} 题</strong>
                </li>
              ))}
            </ul>
          </Card>
          <Card className="p-5">
            <h2 className="font-bold">错误类型</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {analysis.error_types.map((item) => (
                <li
                  className="flex justify-between rounded-lg bg-slate-50 p-3"
                  key={item.name}
                >
                  <span>{item.name}</span>
                  <strong>{item.count} 题</strong>
                </li>
              ))}
            </ul>
          </Card>
          <Card className="p-5 md:col-span-2">
            <h2 className="font-bold">建议下一步</h2>
            <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm">
              {analysis.suggested_actions.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ol>
            <Link
              className="mt-4 inline-block font-medium text-blue-700 hover:underline"
              href="/student/wrong-questions"
            >
              查看错题并申请复核 →
            </Link>
          </Card>
        </div>
      )}
    </div>
  );
}

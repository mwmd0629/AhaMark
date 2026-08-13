"use client";

import { useEffect, useState } from "react";
import {
  Card,
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
  useToast,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type StudentLearningAnalysis,
} from "@/lib/student-api";
import { formatDateTime } from "@/lib/student-format";

function AnalysisList({
  title,
  items,
  tone,
}: {
  title: string;
  items?: string[];
  tone: string;
}) {
  return (
    <section className={`rounded-xl border p-4 ${tone}`}>
      <h3 className="font-bold">{title}</h3>
      {items?.length ? (
        <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-[var(--text-secondary)]">
          暂无可展示内容。
        </p>
      )}
    </section>
  );
}

export default function StudentLearningAnalysisPage() {
  const toast = useToast();
  const [analyses, setAnalyses] = useState<StudentLearningAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setAnalyses(collectionItems(await studentApi.learningAnalyses()));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学习分析加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);
  const hasRunning = analyses.some(
    (item) => item.status === "queued" || item.status === "running",
  );
  useEffect(() => {
    if (!hasRunning) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [hasRunning]);

  const generate = async () => {
    setGenerating(true);
    setError("");
    try {
      await studentApi.generateLearningAnalysis();
      toast("学习分析任务已提交");
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学习分析任务提交失败。",
      );
    } finally {
      setGenerating(false);
    }
  };
  const analysis =
    analyses.find((item) => item.status === "complete") ?? analyses[0];

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI 学习分析"
        description="基于你自己的已发布成绩与错题生成，不包含其他学生或教师内部数据。"
        eyebrow={
          <span className="mb-2 inline-flex rounded-full bg-purple-50 px-2.5 py-1 text-xs font-semibold text-purple-700">
            AI 生成 · 仅供学习参考
          </span>
        }
        actions={
          <Button
            type="button"
            variant="secondary"
            loading={generating}
            disabled={hasRunning}
            onClick={() => void generate()}
          >
            {hasRunning
              ? "正在生成"
              : analyses.length
                ? "更新分析"
                : "生成分析"}
          </Button>
        }
      />
      <div
        role="note"
        className="rounded-xl border border-purple-200 bg-purple-50 p-4 text-sm leading-6 text-purple-800"
      >
        AI
        可能出错。本页内容不会修改成绩；如果对具体错题判定有疑问，请在错题本继续询问或提交教师人工复核。
      </div>
      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : analysis ? (
        <Card className="p-5">
          <SectionHeader
            title="最近一次分析"
            description={`生成于 ${formatDateTime(analysis.generated_at)} · 使用 ${analysis.source_release_count ?? 0} 个已发布成绩版本`}
          />
          {analysis.status !== "complete" ? (
            <p
              role="status"
              className="mt-5 rounded-xl bg-amber-50 p-4 text-sm text-amber-800"
            >
              学习分析当前状态：{analysis.status}
              。系统完成处理后请刷新页面查看。
            </p>
          ) : (
            <>
              {analysis.summary && (
                <p className="mt-5 whitespace-pre-wrap text-sm leading-7">
                  {analysis.summary}
                </p>
              )}
              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <AnalysisList
                  title="学习优势"
                  items={analysis.strengths}
                  tone="border-emerald-200 bg-emerald-50/60"
                />
                <AnalysisList
                  title="需要加强"
                  items={analysis.weaknesses}
                  tone="border-amber-200 bg-amber-50/60"
                />
                <AnalysisList
                  title="知识漏洞"
                  items={analysis.knowledge_gaps}
                  tone="border-rose-200 bg-rose-50/60"
                />
                <AnalysisList
                  title="下一步建议"
                  items={analysis.recommended_actions}
                  tone="border-blue-200 bg-blue-50/60"
                />
              </div>
            </>
          )}
        </Card>
      ) : (
        <EmptyState
          title="暂无学习分析"
          description="有足够的已发布成绩和错题数据后，系统会生成你的个人学习分析。"
          icon="analytics"
        />
      )}
    </div>
  );
}

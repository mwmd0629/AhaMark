"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { MathValidationEvidence } from "@/components/math-validation-evidence";
import { AIGradingReview } from "@/components/ai-grading-review";
import { mathValidationApi, type MathValidationJob } from "@/lib/api";

export default function MathValidationReviewPage() {
  const { batchId, answerId } = useParams<{
    batchId: string;
    answerId: string;
  }>();
  const [jobs, setJobs] = useState<MathValidationJob[]>([]);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setJobs(await mathValidationApi.listForAnswer(answerId));
  }, [answerId]);

  useEffect(() => {
    load().catch(() => setMessage("无法加载数学验证证据"));
  }, [load]);

  const current = jobs.find((job) => !job.stale) ?? jobs[0];

  return (
    <main className="space-y-6">
      <header>
        <Link href={`/grading/${batchId}/review`} className="text-sm underline">
          返回教师复核
        </Link>
        <h1 className="mt-2 text-2xl font-bold">数学验证复核</h1>
        <p className="text-sm text-slate-600">
          自动建议分、教师实际录分和正式成绩是三个独立状态。
        </p>
      </header>

      {message && <p role="status">{message}</p>}
      {current ? (
        <>
          <section className="grid gap-3 rounded-xl border bg-white p-4 md:grid-cols-2">
            <p>固定 scoring_input_version：{current.scoring_input_version}</p>
            <p>Rubric 版本：{current.rubric_version_id}</p>
            <p>标准答案版本：{current.reference_answer_version_id}</p>
            <p>任务状态：{current.status}</p>
            <p>教师实际录分：请在教师复核页录入</p>
            <p>正式成绩：仅在教师确认与发布后生成</p>
          </section>
          <MathValidationEvidence job={current} />
          <AIGradingReview
            answerId={answerId}
            rubricVersionId={current.rubric_version_id}
          />
          <section className="rounded-xl border bg-white p-4">
            <h2 className="font-semibold">单项重试</h2>
            <div className="mt-2 flex flex-wrap gap-2">
              {current.results.map((result) => (
                <button
                  key={result.criterion_id}
                  type="button"
                  disabled={current.stale}
                  onClick={() =>
                    mathValidationApi
                      .retry(current.id, result.criterion_id)
                      .then(load)
                      .catch(() => setMessage("单项重试失败"))
                  }
                  className="rounded border px-3 py-2"
                >
                  重试 {result.criterion_id}
                </button>
              ))}
            </div>
          </section>
          {jobs.length > 1 && (
            <section className="rounded-xl border bg-white p-4">
              <h2 className="font-semibold">历史与 stale 结果</h2>
              {jobs.map((job) => (
                <p key={job.id}>
                  {job.id} · {job.status} ·{" "}
                  {job.stale ? "stale（不作为当前建议）" : "当前"}
                </p>
              ))}
            </section>
          )}
        </>
      ) : (
        <p>尚无验证任务。教师可在识别证据确认后创建验证任务。</p>
      )}
    </main>
  );
}

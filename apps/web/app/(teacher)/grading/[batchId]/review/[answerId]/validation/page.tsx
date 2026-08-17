"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { MathValidationEvidence } from "@/components/math-validation-evidence";
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
    load().catch(() => setMessage("无法加载验证记录"));
  }, [load]);

  const current = jobs.find((job) => !job.stale) ?? jobs[0];

  return (
    <main className="space-y-6">
      <header>
        <Link href={`/grading/${batchId}/review`} className="text-sm underline">
          返回检查结果
        </Link>
        <h1 className="mt-2 text-2xl font-bold">验证详情</h1>
        <p className="text-sm text-slate-600">仅在需要时查看。</p>
      </header>

      {message && <p role="status">{message}</p>}
      {current ? (
        <>
          <MathValidationEvidence job={current} />
          <section className="rounded-xl border bg-white p-4">
            <h2 className="font-semibold">重试检查</h2>
            <div className="mt-2 flex flex-wrap gap-2">
              {current.results.map((result, index) => (
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
                  重试第 {index + 1} 项
                </button>
              ))}
            </div>
          </section>
          <details className="rounded-xl border bg-white p-4 text-sm">
            <summary className="cursor-pointer font-semibold">技术信息</summary>
            <div className="mt-2 space-y-1 text-slate-600">
              <p>输入版本：{current.scoring_input_version}</p>
              <p>评分标准集：{current.structured_rubric_set_id}</p>
              <p>评分标准版本：{current.structured_rubric_version_id}</p>
              <p>参考答案版本：{current.reference_answer_version_id}</p>
              <p>状态：{current.status}</p>
            </div>
            {jobs.length > 1 && (
              <div className="mt-3 border-t pt-3">
                <h2 className="font-semibold">历史记录</h2>
                {jobs.map((job) => (
                  <p key={job.id}>
                    {job.id} · {job.status} · {job.stale ? "已失效" : "当前"}
                  </p>
                ))}
              </div>
            )}
          </details>
        </>
      ) : (
        <p>暂无验证记录。</p>
      )}
    </main>
  );
}

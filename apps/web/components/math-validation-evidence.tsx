import type { MathValidationJob } from "@/lib/api";

export function MathValidationEvidence({ job }: { job: MathValidationJob }) {
  return (
    <section aria-label="数学验证证据" className="rounded-xl border p-4">
      <header className="flex items-center justify-between">
        <h3 className="font-semibold">确定性数学验证</h3>
        {job.stale && (
          <span className="rounded bg-amber-100 px-2 py-1">已过期</span>
        )}
      </header>
      <p className="mt-2 text-sm">
        自动建议总分：{job.suggested_total}。教师最终录分和正式成绩需独立确认。
      </p>
      <div className="mt-3 space-y-2">
        {job.results.map((result) => (
          <article key={result.id} className="rounded border p-3 text-sm">
            <div className="flex gap-3">
              <strong>{result.result}</strong>
              <span>建议分：{result.suggested_points ?? "待教师判断"}</span>
            </div>
            {result.diagnostics.reason != null && (
              <p className="mt-1 text-red-700">
                原因：{String(result.diagnostics.reason)}
              </p>
            )}
            <p className="mt-1 text-slate-600">
              方法：{result.comparison_method}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

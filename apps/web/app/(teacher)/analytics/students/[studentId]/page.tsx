"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { analyticsApi } from "@/lib/api";
import { Card, EmptyState, PageHeader, Skeleton } from "@/components/ui";

type StudentAnalytics = {
  student: {
    name: string;
    student_number: string;
    status: string;
    current_class?: string;
  };
  current?: Record<string, unknown>;
  history: Record<string, unknown>[];
  questions: Record<string, unknown>[];
  teacher_comments: Record<string, unknown>[];
  score_revisions: Record<string, unknown>[];
  report_jobs: Record<string, unknown>[];
};

export default function StudentAnalyticsPage() {
  const { studentId } = useParams<{ studentId: string }>();
  const [data, setData] = useState<StudentAnalytics>();
  const [error, setError] = useState("");
  const [knowledgePoints, setKnowledgePoints] = useState<
    Record<string, unknown>[]
  >([]);
  const [knowledgeRule, setKnowledgeRule] = useState("");
  useEffect(() => {
    analyticsApi
      .student(studentId)
      .then((value) => setData(value as unknown as StudentAnalytics))
      .catch(() => setError("学生分析不存在或无权访问。"));
  }, [studentId]);
  if (error)
    return (
      <Card className="border-red-300 p-5 text-red-700" role="alert">
        {error}
      </Card>
    );
  if (!data) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-6">
      <PageHeader
        title={`${data.student.name} · 学情详情`}
        description={`学号 ${data.student.student_number} · ${data.student.current_class ?? "历史班级"} · ${data.student.status}`}
      />
      {!data.current ? (
        <EmptyState
          icon="analytics"
          title="暂无已完成成绩"
          description="未完成成绩不会显示为零分。"
        />
      ) : (
        <Card className="p-5">
          <h2 className="font-bold">当前发布成绩</h2>
          <p>
            {String(data.current.total_score)} /{" "}
            {String(data.current.max_score)} ·{" "}
            {(Number(data.current.score_rate) * 100).toFixed(1)}%
          </p>
          <p className="text-sm text-slate-500">
            GradeRelease {String(data.current.grade_release_id)} · ScoreSnapshot{" "}
            {String(data.current.score_snapshot_id)}
          </p>
        </Card>
      )}
      <LineTrend
        title="学生历史得分率趋势"
        points={data.history}
        rateKey="score_rate"
      />
      <Section
        title="各题表现、错误类型、知识点与最终评语"
        rows={data.questions}
      />
      <Section title="教师确认评语" rows={data.teacher_comments} />
      <Card className="p-5">
        <h2 className="mb-3 text-lg font-bold">学生知识点历史趋势</h2>
        <div className="flex flex-wrap gap-2">
          {uniqueKnowledge(data.questions).map((point) => (
            <button
              className="rounded border px-3 py-2 text-blue-700"
              key={point.id}
              onClick={async () => {
                const result = (await analyticsApi.studentKnowledgeTrend(
                  studentId,
                  point.id,
                )) as {
                  items: Record<string, unknown>[];
                  scoring_rule: string;
                };
                setKnowledgePoints(result.items);
                setKnowledgeRule(result.scoring_rule);
              }}
            >
              {point.name}
            </button>
          ))}
        </div>
        {knowledgePoints.length ? (
          <LineTrend
            title="知识点掌握率"
            points={knowledgePoints}
            rateKey="mastery_rate"
          />
        ) : (
          <p className="mt-3 text-slate-500">
            请选择知识点；没有相关题目的作业不会生成零值点。
          </p>
        )}
        {knowledgeRule && (
          <p className="mt-2 text-sm text-slate-600">
            计分规则：{knowledgeRule}
          </p>
        )}
      </Card>
      <Section title="ScoreRevision 修改历史" rows={data.score_revisions} />
      <ReportSection
        jobs={data.report_jobs}
        onJobs={(jobs) => setData({ ...data, report_jobs: jobs })}
      />
    </div>
  );
}

function Section({
  title,
  rows,
}: {
  title: string;
  rows: Record<string, unknown>[];
}) {
  return (
    <Card className="overflow-x-auto p-5">
      <h2 className="mb-3 text-lg font-bold">{title}</h2>
      {rows.length ? (
        <pre className="whitespace-pre-wrap text-xs">
          {JSON.stringify(rows, null, 2)}
        </pre>
      ) : (
        <p className="text-slate-500">暂无数据</p>
      )}
    </Card>
  );
}
function ReportSection({
  jobs,
  onJobs,
}: {
  jobs: Record<string, unknown>[];
  onJobs: (jobs: Record<string, unknown>[]) => void;
}) {
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "生成中",
    completed: "已完成",
    partially_completed: "部分完成",
    failed: "失败",
    expired: "已过期",
  };
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-lg font-bold">报告状态</h2>
      {message && (
        <p className="mb-3 text-sm text-emerald-700" role="status">
          {message}
        </p>
      )}
      {jobs.length ? (
        <ul className="space-y-3">
          {jobs.map((job) => (
            <li className="rounded border p-3" key={String(job.id)}>
              <strong>{String(job.report_type)}</strong> ·{" "}
              {labels[String(job.status)] ?? String(job.status)} · 真实进度{" "}
              {String(job.progress)}%
              <div className="text-sm text-slate-500">
                创建 {String(job.created_at)} · 完成{" "}
                {String(job.completed_at ?? "—")} · GradeRelease{" "}
                {String(job.grade_release_id)}
              </div>
              {job.error_message ? (
                <p className="text-red-700">{String(job.error_message)}</p>
              ) : null}
              <div className="mt-2 flex gap-2">
                {Boolean(job.download_available) && (
                  <button
                    className="text-blue-700 underline disabled:opacity-50"
                    disabled={busy === String(job.id)}
                    onClick={async () => {
                      setBusy(String(job.id));
                      try {
                        const result = await analyticsApi.reportDownload(
                          String(job.id),
                        );
                        window.location.assign(result.url);
                      } finally {
                        setBusy("");
                      }
                    }}
                  >
                    重新获取短期下载地址
                  </button>
                )}
                {["failed", "expired", "partially_completed"].includes(
                  String(job.status),
                ) && (
                  <button
                    className="rounded border px-3 py-1 text-blue-700 disabled:opacity-50"
                    disabled={busy === String(job.id)}
                    onClick={async () => {
                      setBusy(String(job.id));
                      try {
                        const replacement = await analyticsApi.retryReport(
                          String(job.id),
                        );
                        onJobs([replacement, ...jobs]);
                        setMessage("已创建新的报告任务；原任务不会恢复。");
                      } finally {
                        setBusy("");
                      }
                    }}
                  >
                    重新生成
                  </button>
                )}
                {job.status === "running" && (
                  <span>Worker 正在处理真实进度。</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-slate-500">尚未生成个人报告</p>
      )}
    </Card>
  );
}

function uniqueKnowledge(questions: Record<string, unknown>[]) {
  const values = new Map<string, string>();
  for (const question of questions)
    for (const point of (question.knowledge_points ?? []) as Array<{
      id: string;
      name: string;
    }>)
      values.set(point.id, point.name);
  return [...values].map(([id, name]) => ({ id, name }));
}

function LineTrend({
  title,
  points,
  rateKey,
}: {
  title: string;
  points: Record<string, unknown>[];
  rateKey: string;
}) {
  const coordinates = points
    .map(
      (point, index) =>
        `${points.length === 1 ? 50 : (index / (points.length - 1)) * 100},${100 - Number(point[rateKey]) * 100}`,
    )
    .join(" ");
  return (
    <Card className="mt-3 overflow-x-auto p-4">
      <h3 className="font-bold">{title}</h3>
      {points.length < 3 && (
        <p className="text-sm text-amber-700">历史点较少，请谨慎解释趋势。</p>
      )}
      {points.length ? (
        <>
          <svg
            className="mt-3 h-52 min-w-[36rem] w-full border-b border-l"
            viewBox="0 0 100 100"
            role="img"
            aria-label={`${title}，纵轴 0 到 100%`}
            preserveAspectRatio="none"
          >
            <polyline
              points={coordinates}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            {points.map((point, index) => (
              <circle
                key={String(point.grade_release_id)}
                cx={
                  points.length === 1 ? 50 : (index / (points.length - 1)) * 100
                }
                cy={100 - Number(point[rateKey]) * 100}
                r="2"
                tabIndex={0}
              >
                <title>
                  {String(point.assignment_name)}：
                  {(Number(point[rateKey]) * 100).toFixed(1)}%，
                  {String(point.total_score ?? point.score)} /{" "}
                  {String(point.max_score)}
                </title>
              </circle>
            ))}
          </svg>
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr>
                <th>作业</th>
                <th>发布时间</th>
                <th>实际/可得</th>
                <th>得分率/掌握率</th>
                <th>发布版本</th>
                <th>参与人数</th>
                <th>关联题目</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={String(point.grade_release_id)}>
                  <td>{String(point.assignment_name)}</td>
                  <td>{String(point.released_at)}</td>
                  <td>
                    {String(point.total_score ?? point.score)} /{" "}
                    {String(point.max_score)}
                  </td>
                  <td>{(Number(point[rateKey]) * 100).toFixed(1)}%</td>
                  <td>{String(point.grade_release_id)}</td>
                  <td>{String(point.participant_count ?? 1)}</td>
                  <td>
                    {Array.isArray(point.question_ids)
                      ? point.question_ids.join(", ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="text-slate-500">暂无趋势数据；缺失作业不记零。</p>
      )}
    </Card>
  );
}

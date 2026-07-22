"use client";

import { useEffect, useState } from "react";
import { analyticsApi, assignmentsApi, GradeRelease } from "@/lib/api";
import {
  Card,
  EmptyState,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";

type Metrics = {
  participant_count: number;
  average_score: number | null;
  highest_score: number | null;
  lowest_score: number | null;
  median_score: number | null;
  score_distribution: Record<string, number>;
  questions: Array<{
    question_id: string;
    question_number: string;
    participants: number;
    score_rate: number;
    full_rate: number;
    zero_rate: number;
    correct_rate: number | null;
  }>;
  knowledge_points: Array<{
    knowledge_point_id: string;
    mastery_rate: number;
    sample_count: number;
  }>;
  error_types: Array<{ code: string; count: number }>;
};
type Insight = {
  id: string;
  provider: string;
  provider_label?: string;
  status: string;
  content: { recommendations?: string[]; original_recommendations?: string[] };
  evidence: Record<string, unknown>[];
  updated_at?: string;
};

export default function AnalyticsPage() {
  const [assignments, setAssignments] = useState<
    Array<{ id: string; title: string }>
  >([]);
  const [assignmentId, setAssignmentId] = useState("");
  const [releases, setReleases] = useState<GradeRelease[]>([]);
  const [releaseId, setReleaseId] = useState("");
  const [metrics, setMetrics] = useState<Metrics>();
  const [snapshotId, setSnapshotId] = useState("");
  const [trends, setTrends] = useState<Record<string, unknown>[]>([]);
  const [knowledgeTrends, setKnowledgeTrends] = useState<
    Record<string, unknown>[]
  >([]);
  const [knowledgeRule, setKnowledgeRule] = useState("");
  const [insight, setInsight] = useState<Insight>();
  const [toast, setToast] = useState("");
  const [drilldown, setDrilldown] = useState<{
    title: string;
    rows: Record<string, unknown>[];
    total: number;
  }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    assignmentsApi
      .list("status=completed")
      .then((page) => setAssignments(page.items))
      .catch(() => setError("无法加载作业，请检查 API 服务。"))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (!assignmentId) return;
    setMetrics(undefined);
    analyticsApi
      .releases(assignmentId)
      .then(setReleases)
      .catch(() => setError("无法加载成绩发布历史。"));
  }, [assignmentId]);

  async function generate() {
    if (!releaseId) return;
    setLoading(true);
    setError("");
    try {
      const result = await analyticsApi.generate(releaseId);
      setSnapshotId(result.id);
      setMetrics(result.metrics as Metrics);
      const selected = releases.find((item) => item.id === releaseId);
      if (selected) {
        const history = await analyticsApi.classTrends(selected.class_id);
        setTrends(history.items);
      }
    } catch {
      setError("分析生成失败：发布数据可能不完整或无权访问。 ");
    } finally {
      setLoading(false);
    }
  }

  async function createInsight() {
    setLoading(true);
    try {
      setInsight((await analyticsApi.insight(snapshotId)) as Insight);
      setToast("规则型教学建议已生成");
    } catch {
      setError("教学建议生成失败。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="学情分析"
        description="所有指标由后端基于成绩发布批次固定的 complete 成绩快照计算。"
      />
      <Card className="grid gap-4 p-5 md:grid-cols-3">
        <Select
          label="作业"
          value={assignmentId}
          onChange={(event) => setAssignmentId(event.target.value)}
        >
          <option value="">请选择作业</option>
          {assignments.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </Select>
        <Select
          label="发布版本"
          value={releaseId}
          onChange={(event) => setReleaseId(event.target.value)}
        >
          <option value="">请选择发布版本</option>
          {releases.map((item) => (
            <option key={item.id} value={item.id}>
              版本 {item.version} · {item.status}
            </option>
          ))}
        </Select>
        <button
          className="mt-6 rounded-xl bg-blue-600 px-4 py-2 font-semibold text-white disabled:opacity-50"
          disabled={!releaseId || loading}
          onClick={generate}
        >
          生成 / 刷新分析
        </button>
      </Card>
      {loading && <Skeleton className="h-40 w-full" />}
      {error && (
        <Card className="border-red-300 p-5 text-red-700" role="alert">
          {error}
        </Card>
      )}
      {toast && (
        <Card className="border-emerald-300 p-4 text-emerald-800" role="status">
          {toast}
        </Card>
      )}
      {!loading && !metrics && !error && (
        <EmptyState
          icon="analytics"
          title="请选择成绩发布版本"
          description="没有发布数据时不会使用临时评分或生成虚假分析。"
        />
      )}
      {metrics && (
        <>
          {metrics.participant_count < 5 && (
            <Card className="border-amber-300 p-4 text-amber-800">
              样本量仅 {metrics.participant_count}，请谨慎解释结果。
            </Card>
          )}
          <div className="grid gap-4 md:grid-cols-5">
            {[
              ["参与人数", metrics.participant_count],
              ["平均分", metrics.average_score],
              ["最高分", metrics.highest_score],
              ["最低分", metrics.lowest_score],
              ["中位数", metrics.median_score],
            ].map(([label, value]) => (
              <Card className="p-4" key={String(label)}>
                <div className="text-sm text-slate-500">{label}</div>
                <div className="text-2xl font-bold">{value ?? "无数据"}</div>
              </Card>
            ))}
          </div>
          <DataCard
            title="分数分布"
            headers={["分数段", "人数", "分布图（0–100%）"]}
            rows={Object.entries(metrics.score_distribution).map(
              ([band, count]) => [
                <DrillButton
                  key={band}
                  label={band}
                  onClick={() =>
                    loadDrill(
                      `分数段 ${band}`,
                      analyticsApi.scoreBand(snapshotId, band),
                    )
                  }
                />,
                count,
                <Bar
                  key={`${band}-bar`}
                  value={count / metrics.participant_count}
                />,
              ],
            )}
          />
          <DataCard
            title="题目分析"
            headers={["题号", "样本", "得分率", "满分率", "零分率", "正确率"]}
            rows={metrics.questions.map((q) => [
              <DrillButton
                key={q.question_id}
                label={q.question_number}
                onClick={() =>
                  loadDrill(
                    `第 ${q.question_number} 题`,
                    analyticsApi.question(snapshotId, q.question_id),
                  )
                }
              />,
              q.participants,
              percent(q.score_rate),
              percent(q.full_rate),
              percent(q.zero_rate),
              q.correct_rate === null
                ? "主观题不定义"
                : percent(q.correct_rate),
            ])}
          />
          <DataCard
            title="知识点掌握率"
            headers={["知识点 ID", "掌握率", "样本"]}
            rows={metrics.knowledge_points.map((kp) => [
              <DrillButton
                key={kp.knowledge_point_id}
                label={kp.knowledge_point_id}
                onClick={() => loadKnowledge(kp.knowledge_point_id)}
              />,
              percent(kp.mastery_rate),
              kp.sample_count,
            ])}
          />
          <DataCard
            title="教师确认错误类型"
            headers={["错误代码", "频次"]}
            rows={metrics.error_types.map((item) => [
              <DrillButton
                key={item.code}
                label={item.code}
                onClick={() =>
                  loadDrill(
                    `错误类型 ${item.code}`,
                    analyticsApi.errorType(snapshotId, item.code),
                  )
                }
              />,
              item.count,
            ])}
          />
          <TrendCard title="班级历史得分率趋势" points={trends} />
          {knowledgeTrends.length > 0 && (
            <TrendCard
              title="班级知识点历史趋势"
              points={knowledgeTrends}
              rateKey="mastery_rate"
            />
          )}
          {knowledgeRule && (
            <p className="text-sm text-slate-600">计分规则：{knowledgeRule}</p>
          )}
          <Card className="p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold">教学建议</h2>
                <p className="text-sm text-slate-600">
                  规则型教学建议，不代表真实 AI 深度分析。
                </p>
              </div>
              {!insight && (
                <button
                  className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
                  disabled={loading}
                  onClick={createInsight}
                >
                  生成规则建议
                </button>
              )}
            </div>
            {insight && (
              <InsightEditor
                insight={insight}
                busy={loading}
                onChange={setInsight}
                onBusy={setLoading}
                onToast={setToast}
                onError={setError}
              />
            )}
          </Card>
          {drilldown && (
            <Card className="p-5" aria-live="polite">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-lg font-bold">
                  {drilldown.title}（{drilldown.total}）
                </h2>
                <button
                  className="rounded border px-3 py-1"
                  onClick={() => setDrilldown(undefined)}
                >
                  关闭
                </button>
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">
                {JSON.stringify(drilldown.rows, null, 2)}
              </pre>
            </Card>
          )}
        </>
      )}
    </div>
  );

  async function loadDrill(
    title: string,
    promise: Promise<{ items: Record<string, unknown>[]; total: number }>,
  ) {
    setError("");
    try {
      const result = await promise;
      setDrilldown({ title, rows: result.items, total: result.total });
    } catch {
      setError("下钻加载失败或无权访问。");
    }
  }
  async function loadKnowledge(knowledgePointId: string) {
    const selected = releases.find((item) => item.id === releaseId);
    await loadDrill(
      "知识点下钻",
      analyticsApi.knowledgePoint(snapshotId, knowledgePointId),
    );
    if (selected) {
      const result = await analyticsApi.classKnowledgeTrend(
        selected.class_id,
        knowledgePointId,
      );
      setKnowledgeTrends(result.items);
      setKnowledgeRule(result.scoring_rule);
    }
  }
}

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
function DataCard({
  title,
  headers,
  rows,
}: {
  title: string;
  headers: string[];
  rows: Array<Array<React.ReactNode>>;
}) {
  return (
    <Card className="overflow-x-auto p-5">
      <h2 className="mb-4 text-lg font-bold">{title}</h2>
      <table className="w-full text-left text-sm">
        <thead>
          <tr>
            {headers.map((header) => (
              <th className="border-b p-2" key={header}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, index) => (
              <tr key={index}>
                {row.map((cell, cellIndex) => (
                  <td className="border-b p-2" key={cellIndex}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td className="p-3 text-slate-500" colSpan={headers.length}>
                无数据
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
  );
}

function DrillButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button className="font-medium text-blue-700 underline" onClick={onClick}>
      {label}
    </button>
  );
}
function Bar({ value }: { value: number }) {
  return (
    <div
      className="h-3 w-40 rounded bg-slate-100"
      role="img"
      aria-label={`${(value * 100).toFixed(1)}%`}
    >
      <div
        className="h-3 rounded bg-blue-600"
        style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }}
      />
    </div>
  );
}
function TrendCard({
  title,
  points,
  rateKey = "average_score_rate",
}: {
  title: string;
  points: Record<string, unknown>[];
  rateKey?: string;
}) {
  return (
    <Card className="overflow-x-auto p-5">
      <h2 className="mb-4 text-lg font-bold">{title}</h2>
      {points.length ? (
        <>
          <div
            className="flex h-48 min-w-[36rem] items-end gap-3 border-b border-l p-3"
            aria-hidden="true"
          >
            {points.map((point) => (
              <div
                key={String(point.analytics_snapshot_id)}
                className="flex flex-1 flex-col items-center justify-end"
              >
                <span className="text-xs">
                  {(Number(point[rateKey]) * 100).toFixed(1)}%
                </span>
                <div
                  className="w-full max-w-16 bg-emerald-600"
                  style={{
                    height: `${Number(point[rateKey]) * 100}%`,
                  }}
                />
              </div>
            ))}
          </div>
          <table className="mt-4 w-full text-sm">
            <thead>
              <tr>
                <th>作业</th>
                <th>发布时间</th>
                <th>参与人数</th>
                <th>平均得分率</th>
                <th>样本变化</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={String(point.analytics_snapshot_id)}>
                  <td>{String(point.assignment_name)}</td>
                  <td>{String(point.released_at ?? "—")}</td>
                  <td>{String(point.participant_count)}</td>
                  <td>{(Number(point[rateKey]) * 100).toFixed(1)}%</td>
                  <td>{point.sample_changed ? "有变化" : "无变化"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="text-slate-500">暂无已发布历史数据</p>
      )}
    </Card>
  );
}

function InsightEditor({
  insight,
  busy,
  onChange,
  onBusy,
  onToast,
  onError,
}: {
  insight: Insight;
  busy: boolean;
  onChange: (value: Insight) => void;
  onBusy: (value: boolean) => void;
  onToast: (value: string) => void;
  onError: (value: string) => void;
}) {
  const [text, setText] = useState(
    (insight.content.recommendations ?? []).join("\n"),
  );
  const disabled =
    busy ||
    ["confirmed", "stale", "superseded", "invalid"].includes(insight.status);
  async function act(action: "save" | "confirm" | "regenerate" | "invalidate") {
    onBusy(true);
    onError("");
    try {
      const value =
        action === "save"
          ? await analyticsApi.editInsight(
              insight.id,
              text
                .split("\n")
                .map((x) => x.trim())
                .filter(Boolean),
            )
          : action === "confirm"
            ? await analyticsApi.confirmInsight(insight.id)
            : action === "regenerate"
              ? await analyticsApi.regenerateInsight(insight.id)
              : await analyticsApi.invalidateInsight(insight.id);
      const next = value as Insight;
      onChange(next);
      setText((next.content.recommendations ?? []).join("\n"));
      onToast(
        {
          save: "建议草稿已保存",
          confirm: "建议已确认",
          regenerate: "已生成新的规则建议",
          invalidate: "建议已标记失效",
        }[action],
      );
    } catch {
      onError("教学建议操作失败，状态可能已变化或 evidence 无效。");
    } finally {
      onBusy(false);
    }
  }
  return (
    <div className="mt-4 space-y-3">
      <div className="flex flex-wrap gap-3 text-sm">
        <span>类型：规则型教学建议</span>
        <span>来源：{insight.provider}</span>
        <span>状态：{insight.status}</span>
        <span>修改时间：{insight.updated_at ?? "—"}</span>
      </div>
      {["stale", "superseded"].includes(insight.status) && (
        <p className="text-amber-700">该建议已过期，请重新生成。</p>
      )}
      <label className="block text-sm font-medium">
        建议内容
        <textarea
          className="mt-1 min-h-32 w-full rounded border p-3"
          value={text}
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <details>
        <summary className="cursor-pointer font-medium">
          真实 evidence（{insight.evidence.length}）
        </summary>
        <pre className="mt-2 overflow-auto text-xs">
          {JSON.stringify(insight.evidence, null, 2)}
        </pre>
      </details>
      {insight.content.original_recommendations && (
        <details>
          <summary>原始生成内容</summary>
          <ul>
            {insight.content.original_recommendations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50"
          disabled={disabled || !text.trim()}
          onClick={() => act("save")}
        >
          保存草稿
        </button>
        <button
          className="rounded bg-emerald-700 px-3 py-2 text-white disabled:opacity-50"
          disabled={disabled}
          onClick={() => act("confirm")}
        >
          确认
        </button>
        <button
          className="rounded border px-3 py-2 disabled:opacity-50"
          disabled={busy}
          onClick={() => act("regenerate")}
        >
          重新生成
        </button>
        <button
          className="rounded border border-red-400 px-3 py-2 text-red-700 disabled:opacity-50"
          disabled={busy || insight.status === "invalid"}
          onClick={() => act("invalidate")}
        >
          标记失效
        </button>
      </div>
    </div>
  );
}

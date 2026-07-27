"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, Select, useToast } from "@/components/ui";
import {
  ApiError,
  assignmentReviewApi,
  type AssignmentReadinessRecord,
  type AssignmentRecord,
  type AssignmentReviewItemRecord,
  type AssignmentReviewSessionRecord,
} from "@/lib/api";

const confirmations = [
  ["classes", "确认班级"],
  ["due_at", "确认截止时间"],
  ["total_score", "确认总分"],
  ["file_roles", "确认文件角色"],
  ["answer_sources", "确认答案来源"],
  ["paper_version", "确认 PaperVersion"],
  ["reference_answers", "确认答案版本"],
  ["structured_rubrics", "确认 Structured Rubric"],
] as const;

export function AssignmentCentralReview({
  item,
  onNavigate,
  onPublished,
}: {
  item: AssignmentRecord;
  onNavigate: (step: number) => void;
  onPublished: () => void;
}) {
  const toast = useToast();
  const [session, setSession] = useState<AssignmentReviewSessionRecord>();
  const [items, setItems] = useState<AssignmentReviewItemRecord[]>([]);
  const [readiness, setReadiness] = useState<AssignmentReadinessRecord>();
  const [severity, setSeverity] = useState("all");
  const [section, setSection] = useState("all");
  const [busy, setBusy] = useState(false);
  const [bindingId, setBindingId] = useState<string>();

  const load = useCallback(
    async (candidate?: AssignmentReviewSessionRecord) => {
      const active = candidate ?? session;
      if (!active) return;
      const [fresh, rows] = await Promise.all([
        assignmentReviewApi.get(active.id),
        assignmentReviewApi.items(active.id),
      ]);
      setSession(fresh);
      setItems(rows.items);
    },
    [session],
  );

  useEffect(() => {
    assignmentReviewApi
      .list(item.id)
      .then((result) => {
        const active = result.items.find(
          (row) => !["stale", "invalidated"].includes(row.status),
        );
        if (active) void load(active);
      })
      .catch(() => undefined);
  }, [item.id, load]);

  const act = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await fn();
      toast(message);
      await load();
    } catch (error) {
      toast(error instanceof ApiError ? error.message : "操作失败", "error");
    } finally {
      setBusy(false);
    }
  };
  const visible = useMemo(
    () =>
      items.filter(
        (row) =>
          (severity === "all" || row.severity === severity) &&
          (section === "all" || row.section === section),
      ),
    [items, severity, section],
  );
  const sections = [...new Set(items.map((row) => row.section))].sort();

  if (!session) {
    return (
      <Card className="space-y-4 p-6">
        <h2 className="font-bold">集中审查中心</h2>
        <p>创建会话会固定当前生成与版本输入，不会自动发布。</p>
        <Button
          loading={busy}
          onClick={() =>
            act(async () => {
              const created = await assignmentReviewApi.create(item.id);
              setSession(created);
              await load(created);
            }, "集中审查会话已创建")
          }
        >
          开始集中审查
        </Button>
      </Card>
    );
  }

  return (
    <Card className="space-y-5 p-6">
      <div>
        <h2 className="font-bold">集中审查中心</h2>
        <p className="text-sm text-slate-600">
          generation {session.generation} · DraftRevision{" "}
          {session.draft_revision_id} · PaperVersion {session.paper_version_id}{" "}
          · legacy {session.legacy_rubric_version_id ?? "未绑定"} ·{" "}
          {session.status}
        </p>
      </div>
      <div className="grid grid-cols-3 gap-3" aria-label="风险汇总">
        <div className="rounded bg-emerald-50 p-3">
          绿色 {session.counts.info}
        </div>
        <div className="rounded bg-amber-50 p-3">
          黄色 {session.counts.warning}
        </div>
        <div className="rounded bg-red-50 p-3">
          红色 {session.counts.blocking}
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <Select
          aria-label="按风险过滤"
          value={severity}
          onChange={(event) => setSeverity(event.target.value)}
        >
          <option value="all">全部风险</option>
          <option value="blocking">红色</option>
          <option value="warning">黄色</option>
          <option value="info">绿色</option>
        </Select>
        <Select
          aria-label="按分区过滤"
          value={section}
          onChange={(event) => setSection(event.target.value)}
        >
          <option value="all">全部分区</option>
          {sections.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </Select>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            act(
              () =>
                assignmentReviewApi.refresh(session.id, session.review_version),
              "审查已刷新",
            )
          }
        >
          刷新审查
        </Button>
      </div>
      <ul className="space-y-2">
        {visible.map((review) => (
          <li key={review.id} className="rounded-xl border p-3">
            <div className="flex items-center justify-between gap-2">
              <strong>
                {review.severity === "blocking"
                  ? "红色"
                  : review.severity === "warning"
                    ? "黄色"
                    : "绿色"}{" "}
                · {review.section} · {review.title}
              </strong>
              <span>{review.status}</span>
            </div>
            <p className="text-sm">{review.message}</p>
            <details>
              <summary className="cursor-pointer text-sm">证据</summary>
              <pre className="overflow-auto whitespace-pre-wrap text-xs">
                {JSON.stringify(review.evidence, null, 2)}
              </pre>
            </details>
            <div className="mt-2 flex gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  onNavigate(
                    review.section === "files"
                      ? 2
                      : review.section === "pages"
                        ? 3
                        : review.section === "questions"
                          ? 4
                          : ["answers", "rubrics"].includes(review.section)
                            ? 5
                            : 1,
                  )
                }
              >
                前往修改
              </Button>
              {review.severity === "warning" && review.status === "open" && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    act(
                      () =>
                        assignmentReviewApi.disposition(
                          review.id,
                          session.review_version,
                          "acknowledge",
                        ),
                      "黄色风险已确认查看",
                    )
                  }
                >
                  确认已查看
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <div className="space-y-2">
        <h3 className="font-semibold">教师显式确认</h3>
        <div className="flex flex-wrap gap-2">
          {confirmations.map(([kind, label]) => (
            <Button
              key={kind}
              variant="outline"
              disabled={busy}
              onClick={() =>
                act(
                  () =>
                    assignmentReviewApi.confirm(
                      session.id,
                      kind,
                      session.review_version,
                    ),
                  `${label}完成`,
                )
              }
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      <div className="space-y-2">
        <h3 className="font-semibold">legacy Rubric 绑定</h3>
        <div className="flex gap-2">
          <Button
            disabled={busy}
            onClick={() =>
              act(async () => {
                const binding = await assignmentReviewApi.createBinding(
                  session.id,
                  session.review_version,
                );
                setBindingId(binding.id);
              }, "发布评分标准已准备")
            }
          >
            准备发布评分标准
          </Button>
          <Button
            disabled={!bindingId || busy}
            onClick={() =>
              act(
                () =>
                  assignmentReviewApi.confirmBinding(
                    bindingId!,
                    session.review_version,
                  ),
                "legacy 绑定已确认",
              )
            }
          >
            确认绑定
          </Button>
        </div>
      </div>
      <div className="rounded-xl border p-4">
        <h3 className="font-semibold">发布门禁</h3>
        <p>
          班级 {item.classes.length} · 截止时间 {item.due_at ?? "未设置"} · 总分{" "}
          {item.total_score ?? "未设置"}
        </p>
        <div className="mt-3 flex gap-2">
          <Button
            disabled={
              busy || session.counts.blocking > 0 || session.counts.warning > 0
            }
            onClick={() =>
              act(
                async () =>
                  setReadiness(
                    await assignmentReviewApi.prepare(
                      session.id,
                      session.review_version,
                    ),
                  ),
                "发布准备快照已生成；作业尚未发布",
              )
            }
          >
            准备发布
          </Button>
          <Button
            disabled={!readiness || readiness.status !== "ready" || busy}
            onClick={() => {
              if (
                !readiness ||
                !window.confirm(
                  `确认由教师发布？\n班级：${readiness.class_ids.length}\n截止：${readiness.due_at}\n总分：${readiness.total_score}\nPaper：${readiness.paper_version_id}\nRubric：${readiness.legacy_rubric_version_id}`,
                )
              )
                return;
              void act(async () => {
                await assignmentReviewApi.publish(
                  item.id,
                  readiness,
                  item.updated_at,
                );
                onPublished();
              }, "作业已由教师发布");
            }}
          >
            教师确认并发布
          </Button>
        </div>
      </div>
    </Card>
  );
}

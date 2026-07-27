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
import { getReviewCopy } from "@/lib/review-copy";

const confirmations = [
  ["classes", "确认班级"],
  ["due_at", "确认截止时间"],
  ["total_score", "确认总分"],
  ["file_roles", "确认文件角色"],
  ["answer_sources", "确认答案来源"],
  ["paper_version", "确认试卷版本"],
  ["reference_answers", "确认答案版本"],
  ["structured_rubrics", "确认评分标准"],
] as const;

const manuallyResolvableBlockingCodes = new Set([
  "GENERATION_PARTIAL",
  "PAPER_VARIANT_REVIEW",
  "QUESTION_CONFIRMATION_REQUIRED",
  "QUESTION_PAPER_ROLE_UNCONFIRMED",
]);

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
  const [resolvedOpen, setResolvedOpen] = useState(false);

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
          !["stale", "superseded"].includes(row.status) &&
          (severity === "all" || row.severity === severity) &&
          (section === "all" || row.section === section),
      ),
    [items, severity, section],
  );
  const isResolved = (row: AssignmentReviewItemRecord) =>
    ["acknowledged", "resolved", "rejected"].includes(row.status);
  const unresolved = visible
    .filter((row) => !isResolved(row))
    .sort(
      (a, b) =>
        ({ blocking: 0, warning: 1, info: 2 })[a.severity] -
        { blocking: 0, warning: 1, info: 2 }[b.severity],
    );
  const resolved = visible.filter(isResolved);
  const sections = [...new Set(items.map((row) => row.section))].sort();
  const sectionLabels: Record<string, string> = {
    validation: "内容版本",
    classes: "发布班级",
    due_at: "截止时间",
    files: "试卷文件",
    pages: "试卷页面",
    questions: "题目",
    answers: "参考答案",
    rubrics: "评分标准",
    total_score: "分值",
  };

  const renderReview = (review: AssignmentReviewItemRecord) => {
    const copy = getReviewCopy(review.issue_code);
    return (
      <li key={review.id} className="rounded-xl border p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold text-slate-500">
              {review.severity === "blocking"
                ? "影响发布"
                : review.severity === "warning"
                  ? "建议处理"
                  : "提示"}{" "}
              · {sectionLabels[review.section] ?? "其他"}
            </p>
            <strong className="mt-1 block">{copy.title}</strong>
          </div>
          <span
            className={`rounded-full px-2 py-1 text-xs ${
              review.severity === "blocking"
                ? "bg-red-100 text-red-700"
                : review.severity === "warning"
                  ? "bg-amber-100 text-amber-800"
                  : "bg-emerald-100 text-emerald-800"
            }`}
          >
            {isResolved(review) ? "已解决" : "待处理"}
          </span>
        </div>
        <p className="mt-2 text-sm text-slate-700">{copy.message}</p>
        {isResolved(review) && (
          <dl className="mt-3 grid gap-1 rounded-lg bg-slate-50 p-3 text-sm">
            {review.teacher_action && (
              <div>
                <dt className="inline font-medium">处理方式：</dt>
                <dd className="inline">
                  {review.teacher_action === "acknowledge"
                    ? "已确认查看"
                    : review.teacher_action === "resolve_manual"
                      ? "人工检查并解决"
                      : review.teacher_action}
                </dd>
              </div>
            )}
            {review.teacher_note && (
              <div>
                <dt className="inline font-medium">教师备注：</dt>
                <dd className="inline">{review.teacher_note}</dd>
              </div>
            )}
            {review.reviewed_by && (
              <div>
                <dt className="inline font-medium">处理人：</dt>
                <dd className="inline">{review.reviewed_by}</dd>
              </div>
            )}
            {review.reviewed_at && (
              <div>
                <dt className="inline font-medium">处理时间：</dt>
                <dd className="inline">
                  {new Date(review.reviewed_at).toLocaleString("zh-CN")}
                </dd>
              </div>
            )}
          </dl>
        )}
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-slate-600">
            查看技术详情
          </summary>
          <div className="mt-2 rounded bg-slate-950 p-3 text-xs text-slate-100">
            <p>错误码：{review.issue_code}</p>
            <p>问题 ID：{review.id}</p>
            <p>来源哈希：{review.source_hash}</p>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap">
              {JSON.stringify(review.evidence, null, 2)}
            </pre>
          </div>
        </details>
        {!isResolved(review) && (
          <div className="mt-3 flex flex-wrap gap-2">
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
              {copy.action}
            </Button>
            {review.severity === "warning" && (
              <Button
                disabled={busy}
                onClick={() =>
                  act(
                    () =>
                      assignmentReviewApi.disposition(
                        review.id,
                        session!.review_version,
                        "acknowledge",
                      ),
                    "问题已标记为已查看",
                  )
                }
              >
                确认已查看
              </Button>
            )}
            {review.severity === "blocking" &&
              manuallyResolvableBlockingCodes.has(review.issue_code) && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    act(
                      () =>
                        assignmentReviewApi.disposition(
                          review.id,
                          session!.review_version,
                          "resolve_manual",
                        ),
                      "问题已由教师人工检查并解决",
                    )
                  }
                >
                  人工检查并解决
                </Button>
              )}
          </div>
        )}
      </li>
    );
  };

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
          请先处理影响发布的问题，再确认其余内容。版本等排查信息可在技术详情中查看。
        </p>
      </div>
      <div className="grid grid-cols-3 gap-3" aria-label="风险汇总">
        <div className="rounded bg-emerald-50 p-3">
          提示 {session.counts.info}
        </div>
        <div className="rounded bg-amber-50 p-3">
          警告 {session.counts.warning}
        </div>
        <div className="rounded bg-red-50 p-3">
          阻塞 {session.counts.blocking}
          <span className="sr-only">红色 {session.counts.blocking}</span>
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <Select
          aria-label="按风险过滤"
          value={severity}
          onChange={(event) => setSeverity(event.target.value)}
        >
          <option value="all">全部问题</option>
          <option value="blocking">影响发布</option>
          <option value="warning">警告</option>
          <option value="info">提示</option>
        </Select>
        <Select
          aria-label="按分区过滤"
          value={section}
          onChange={(event) => setSection(event.target.value)}
        >
          <option value="all">全部分区</option>
          {sections.map((value) => (
            <option key={value} value={value}>
              {sectionLabels[value] ?? value}
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
      {unresolved.length > 0 && (
        <ul className="space-y-2">{unresolved.map(renderReview)}</ul>
      )}
      {resolved.length > 0 && (
        <section className="rounded-xl border">
          <button
            type="button"
            className="flex w-full items-center justify-between p-4 text-left font-semibold"
            aria-expanded={resolvedOpen}
            onClick={() => setResolvedOpen((open) => !open)}
          >
            <span>已解决 {resolved.length} 项</span>
            <span aria-hidden="true">{resolvedOpen ? "收起" : "展开"}</span>
          </button>
          {resolvedOpen && (
            <ul className="space-y-2 border-t p-3">
              {resolved.map(renderReview)}
            </ul>
          )}
        </section>
      )}
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
        <h3 className="font-semibold">评分标准发布绑定</h3>
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
                "评分标准绑定已确认",
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
          班级 {item.classes.length} · 截止时间 {item.due_at ?? "无截止时间"} ·
          总分 {item.total_score ?? "未设置"}
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
                  `确认由教师发布？\n班级：${readiness.class_ids.length}\n截止：${readiness.due_at ?? "无截止时间"}\n总分：${readiness.total_score}`,
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

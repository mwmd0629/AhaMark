"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import {
  ApiError,
  studentReviewRequestsApi,
  type StudentReviewRequest,
} from "@/lib/api";

type DecisionDraft = {
  resolution: "upheld" | "score_changed" | "needs_information";
  response: string;
  new_score: string;
  new_feedback: string;
};

const emptyDraft: DecisionDraft = {
  resolution: "upheld",
  response: "",
  new_score: "",
  new_feedback: "",
};

export default function TeacherReviewRequestsPage() {
  const [items, setItems] = useState<StudentReviewRequest[]>();
  const [drafts, setDrafts] = useState<Record<string, DecisionDraft>>({});
  const [saving, setSaving] = useState<string>();
  const [error, setError] = useState("");

  const load = useCallback(() => {
    studentReviewRequestsApi
      .teacherList()
      .then(setItems)
      .catch(() => setError("学生复核申请加载失败，请稍后重试。"));
  }, []);
  useEffect(() => load(), [load]);

  const updateDraft = (id: string, patch: Partial<DecisionDraft>) =>
    setDrafts((current) => ({
      ...current,
      [id]: { ...(current[id] || emptyDraft), ...patch },
    }));

  const resolve = async (item: StudentReviewRequest) => {
    const draft = drafts[item.id] || emptyDraft;
    if (!draft.response.trim()) return setError("请填写给学生的处理说明。");
    if (draft.resolution === "score_changed" && !draft.new_score.trim())
      return setError("选择修改分数时必须填写新分数。");
    setSaving(item.id);
    setError("");
    try {
      await studentReviewRequestsApi.resolve(item.id, {
        resolution: draft.resolution,
        response: draft.response.trim(),
        ...(draft.resolution === "score_changed"
          ? {
              new_score: draft.new_score,
              new_feedback: draft.new_feedback.trim() || undefined,
            }
          : {}),
      });
      load();
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "处理失败，请稍后重试。",
      );
    } finally {
      setSaving(undefined);
    }
  };

  if (!items) return <Skeleton className="h-72 w-full" />;
  const open = items.filter((item) =>
    ["pending", "needs_information"].includes(item.status),
  );
  const resolved = items.filter((item) => item.status === "resolved");
  return (
    <div className="space-y-6">
      <PageHeader
        title="学生复核"
        description={`待处理 ${open.length} 条。改分会写入修订记录，需重新确认并发布成绩版本后才对学生生效。`}
      />
      {error && (
        <Card className="border-red-300 p-4 text-red-700">{error}</Card>
      )}
      {!open.length ? (
        <EmptyState
          icon="grading"
          title="没有待处理申请"
          description="学生针对正式发布错题提交申请后会出现在这里。"
        />
      ) : (
        <div className="space-y-4">
          {open.map((item) => {
            const draft = drafts[item.id] || emptyDraft;
            return (
              <Card className="p-5" key={item.id}>
                <div className="flex flex-wrap justify-between gap-3">
                  <div>
                    <h2 className="font-bold">
                      {item.student_name}（{item.student_number}）· 第{" "}
                      {item.question_number} 题
                    </h2>
                    <p className="mt-1 text-sm text-slate-600">
                      {item.class_name} · {item.assignment_title} · 成绩版本 v
                      {item.grade_release_version}
                    </p>
                  </div>
                  <span className="rounded-full bg-amber-50 px-3 py-1 text-sm text-amber-800">
                    {item.status === "needs_information"
                      ? "等待补充后再处理"
                      : "待处理"}
                  </span>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <div className="rounded-xl bg-slate-50 p-4 text-sm">
                    <strong>题目与答案</strong>
                    <p className="mt-2 whitespace-pre-wrap">
                      {item.question_content || "暂无题目文本"}
                    </p>
                    <p className="mt-2 whitespace-pre-wrap text-slate-600">
                      学生答案：{item.student_answer || "暂无答案文本"}
                    </p>
                  </div>
                  <div className="rounded-xl bg-blue-50 p-4 text-sm">
                    <strong>学生说明</strong>
                    <p className="mt-2 whitespace-pre-wrap">{item.message}</p>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <label className="text-sm font-medium">
                    处理结果
                    <select
                      className="mt-2 block w-full rounded-xl border border-slate-300 bg-white p-3"
                      value={draft.resolution}
                      onChange={(event) =>
                        updateDraft(item.id, {
                          resolution: event.target
                            .value as DecisionDraft["resolution"],
                        })
                      }
                    >
                      <option value="upheld">维持原判</option>
                      <option value="score_changed">修改分数</option>
                      <option value="needs_information">请学生补充说明</option>
                    </select>
                  </label>
                  {draft.resolution === "score_changed" && (
                    <label className="text-sm font-medium">
                      新分数
                      <input
                        className="mt-2 block w-full rounded-xl border border-slate-300 p-3"
                        inputMode="decimal"
                        value={draft.new_score}
                        onChange={(event) =>
                          updateDraft(item.id, {
                            new_score: event.target.value,
                          })
                        }
                      />
                    </label>
                  )}
                </div>
                {draft.resolution === "score_changed" && (
                  <label className="mt-3 block text-sm font-medium">
                    新反馈（可选）
                    <textarea
                      className="mt-2 block w-full rounded-xl border border-slate-300 p-3"
                      rows={2}
                      value={draft.new_feedback}
                      onChange={(event) =>
                        updateDraft(item.id, {
                          new_feedback: event.target.value,
                        })
                      }
                    />
                  </label>
                )}
                <label className="mt-3 block text-sm font-medium">
                  给学生的处理说明
                  <textarea
                    className="mt-2 block w-full rounded-xl border border-slate-300 p-3"
                    maxLength={4000}
                    rows={3}
                    value={draft.response}
                    onChange={(event) =>
                      updateDraft(item.id, { response: event.target.value })
                    }
                  />
                </label>
                <Button
                  className="mt-3"
                  loading={saving === item.id}
                  onClick={() => void resolve(item)}
                >
                  确认处理
                </Button>
              </Card>
            );
          })}
        </div>
      )}
      {resolved.length > 0 && (
        <Card className="p-5">
          <h2 className="font-bold">最近已处理</h2>
          <ul className="mt-3 divide-y text-sm">
            {resolved.slice(0, 20).map((item) => (
              <li
                className="flex flex-wrap justify-between gap-2 py-3"
                key={item.id}
              >
                <span>
                  {item.student_name} · {item.assignment_title} · 第{" "}
                  {item.question_number} 题
                </span>
                <span className="text-slate-600">{item.teacher_response}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

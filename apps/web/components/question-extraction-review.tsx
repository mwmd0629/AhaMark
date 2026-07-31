"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui";
import {
  ApiError,
  assignmentGenerationApi,
  type AssignmentDraftRevision,
  type PageOrganizationSuggestion,
  type QuestionExtractionCandidate,
} from "@/lib/api";

export function QuestionExtractionReview({
  revision,
  onChanged,
}: {
  revision: AssignmentDraftRevision;
  onChanged: () => void;
}) {
  const [pages, setPages] = useState<PageOrganizationSuggestion[]>([]);
  const [questions, setQuestions] = useState<QuestionExtractionCandidate[]>([]);
  const [edits, setEdits] = useState<
    Record<string, { number: string; text: string; score: string }>
  >({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const [p, q] = await Promise.all([
        assignmentGenerationApi.listPageOrganization(revision.id),
        assignmentGenerationApi.listQuestionCandidates(revision.id),
      ]);
      setPages(p);
      setQuestions(q);
      setEdits((old) => {
        const next = { ...old };
        for (const item of q)
          next[item.id] ??= {
            number: item.question_number ?? "",
            text: item.content_text ?? "",
            score: item.max_score?.toString() ?? "",
          };
        return next;
      });
      setError("");
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "无法加载页面与题目候选",
      );
    }
  }, [revision.id]);
  useEffect(() => {
    void load();
  }, [load]);
  const guard = {
    expected_draft_revision_edit_version: revision.teacher_edit_version,
    expected_source_snapshot: revision.source_snapshot_hash,
  };
  async function pageAction(item: PageOrganizationSuggestion, action: string) {
    setBusy(true);
    try {
      await assignmentGenerationApi.dispositionPageOrganization(item.id, {
        action,
        expected_teacher_edit_version: item.teacher_edit_version,
        expected_paper_version_id: item.paper_version_id,
        ...guard,
        ...(action === "modify"
          ? {
              teacher_value: {
                page_number: item.suggested_page_number,
                rotation: item.suggested_rotation,
                status: item.suggested_status,
              },
            }
          : {}),
      });
      onChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? `${reason.message}，请刷新后重试`
          : "页面建议处理失败",
      );
    } finally {
      setBusy(false);
    }
  }
  async function questionAction(
    item: QuestionExtractionCandidate,
    action: string,
  ) {
    setBusy(true);
    try {
      const edit = edits[item.id];
      await assignmentGenerationApi.dispositionQuestionCandidate(item.id, {
        action,
        expected_teacher_edit_version: item.teacher_edit_version,
        expected_paper_version_id: item.paper_version_id,
        ...guard,
        ...(action === "modify"
          ? {
              teacher_value: {
                question_number: edit.number,
                content_text: edit.text,
                max_score: edit.score ? Number(edit.score) : null,
              },
            }
          : {}),
      });
      onChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? `${reason.message}，请刷新后重试`
          : "题目候选处理失败",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="grid gap-5" aria-label="页面整理与题目抽取复核">
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <details className="border-t pt-4">
        <summary className="cursor-pointer rounded-lg px-3 py-3 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
          第三步：整理页面（{pages.length} 页）
        </summary>
        <p className="mt-2 text-sm text-[var(--neutral-600)]">
          AI 不会自动排除或删除页面；排序、旋转与排除建议均须教师确认。
        </p>
        <div className="mt-2 grid gap-2">
          {pages.map((item) => (
            <article key={item.id} className="rounded-lg border p-3 text-sm">
              <p>
                原始页 {item.source_page_number ?? "—"} · 当前页{" "}
                {item.current_page_number} · 当前旋转 {item.current_rotation}° /{" "}
                {item.current_status}
              </p>
              <p>
                建议：页 {item.suggested_page_number} ·{" "}
                {item.suggested_rotation}° / {item.suggested_status} · 置信度{" "}
                {(item.confidence * 100).toFixed(0)}%
              </p>
              <p>
                风险：{item.reason_codes.join("、") || "无"} · 状态{" "}
                {item.status}
              </p>
              <details>
                <summary>查看 evidence</summary>
                <pre className="whitespace-pre-wrap">
                  {JSON.stringify(item.evidence, null, 2)}
                </pre>
              </details>
              {item.status === "suggested" && (
                <div className="mt-2 flex gap-2">
                  <Button
                    disabled={busy}
                    onClick={() => void pageAction(item, "accept")}
                    aria-label={`接受第${item.current_page_number}页建议`}
                  >
                    接受
                  </Button>
                  <Button
                    disabled={busy}
                    onClick={() => void pageAction(item, "modify")}
                  >
                    修改后接受
                  </Button>
                  <Button
                    disabled={busy}
                    onClick={() => void pageAction(item, "reject")}
                  >
                    拒绝
                  </Button>
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void pageAction(item, "mark_manual_required")
                    }
                  >
                    标为人工处理
                  </Button>
                </div>
              )}
            </article>
          ))}
        </div>
      </details>
      <div>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">第四步：编辑题目</h3>
          <Button
            disabled={busy || !questions.some((x) => x.server_eligible)}
            onClick={async () => {
              setBusy(true);
              try {
                const first = questions[0];
                if (first)
                  await assignmentGenerationApi.acceptEligibleQuestions(
                    revision.id,
                    {
                      expected_paper_version_id: first.paper_version_id,
                      ...guard,
                    },
                  );
                onChanged();
                await load();
              } finally {
                setBusy(false);
              }
            }}
          >
            确认全部服务器 eligible 题目
          </Button>
        </div>
        <div className="mt-2 grid gap-3">
          {questions.map((item, index) => (
            <details
              key={item.id}
              className="rounded-lg border open:bg-[var(--neutral-50)]"
            >
              <summary className="cursor-pointer rounded-lg px-4 py-4 text-sm font-medium hover:bg-[var(--neutral-100)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
                {item.question_number || `候选 ${index + 1}`} ·{" "}
                {item.max_score == null ? "分值待定" : `${item.max_score} 分`} ·
                状态 {item.status} · 总置信度{" "}
                {(item.overall_confidence * 100).toFixed(0)}%{" "}
                {item.server_eligible ? "· eligible" : "· 需复核"}
              </summary>
              <div className="grid gap-2 px-4 pb-4">
                <label className="block text-sm">
                  题号
                  <input
                    aria-label={`候选${index + 1}题号`}
                    className="mt-1 w-full rounded border p-2"
                    value={edits[item.id]?.number ?? ""}
                    onChange={(e) =>
                      setEdits({
                        ...edits,
                        [item.id]: {
                          ...edits[item.id],
                          number: e.target.value,
                        },
                      })
                    }
                  />
                </label>
                <p className="text-sm">
                  父候选：{item.parent_candidate_id ?? "无"} · 题型{" "}
                  {item.question_type} · 难度 {item.difficulty ?? "未建议"}
                </p>
                <label className="block text-sm">
                  题干
                  <textarea
                    aria-label={`候选${index + 1}题干`}
                    className="mt-1 w-full rounded border p-2"
                    value={edits[item.id]?.text ?? ""}
                    onChange={(e) =>
                      setEdits({
                        ...edits,
                        [item.id]: { ...edits[item.id], text: e.target.value },
                      })
                    }
                  />
                </label>
                <label className="block text-sm">
                  分值（未知保持空）
                  <input
                    aria-label={`候选${index + 1}分值`}
                    type="number"
                    min="0.01"
                    step="0.01"
                    className="mt-1 rounded border p-2"
                    value={edits[item.id]?.score ?? ""}
                    onChange={(e) =>
                      setEdits({
                        ...edits,
                        [item.id]: { ...edits[item.id], score: e.target.value },
                      })
                    }
                  />
                </label>
                {item.content_latex === null && (
                  <p className="text-sm">公式 LaTeX：未生成</p>
                )}
                <p className="text-sm">
                  知识点建议：
                  {item.knowledge_point_suggestions.join("、") || "无"}
                </p>
                <p className="text-sm">
                  风险：{item.warning_codes.join("、") || "无"}
                  {item.manual_required ? " · 必须人工复核" : ""}
                </p>
                <details>
                  <summary className="cursor-pointer">
                    字段级 confidence / evidence / regions
                  </summary>
                  <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs">
                    {JSON.stringify(
                      {
                        field_confidences: item.field_confidences,
                        evidence: item.evidence,
                        regions: item.regions,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
                {item.status === "suggested" && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      disabled={busy}
                      onClick={() => void questionAction(item, "accept")}
                    >
                      接受
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() => void questionAction(item, "modify")}
                    >
                      修改后接受
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() => void questionAction(item, "reject")}
                    >
                      拒绝
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void questionAction(item, "mark_manual_required")
                      }
                    >
                      标为人工处理
                    </Button>
                  </div>
                )}
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

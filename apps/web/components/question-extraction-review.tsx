"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui";
import {
  ApiError,
  assignmentGenerationApi,
  assignmentsApi,
  type AssignmentDraftRevision,
  type PageOrganizationSuggestion,
  type QuestionExtractionCandidate,
  type ReferenceAnswerSourceBinding,
  type TextbookSourceMatch,
} from "@/lib/api";
import {
  QuestionRegionVisualEditor,
  type QuestionRegionEdit,
  type QuestionRegionPage,
} from "./question-region-visual-editor";

export function QuestionExtractionReview({
  revision,
  onChanged,
}: {
  revision: AssignmentDraftRevision;
  onChanged: () => void;
}) {
  const [pages, setPages] = useState<PageOrganizationSuggestion[]>([]);
  const [regionPages, setRegionPages] = useState<QuestionRegionPage[]>([]);
  const [questions, setQuestions] = useState<QuestionExtractionCandidate[]>([]);
  const [referenceBindings, setReferenceBindings] = useState<
    ReferenceAnswerSourceBinding[]
  >([]);
  const [textbookMatches, setTextbookMatches] = useState<TextbookSourceMatch[]>(
    [],
  );
  const [bindingQuestions, setBindingQuestions] = useState<
    Record<string, string>
  >({});
  const [edits, setEdits] = useState<
    Record<string, { number: string; text: string; score: string }>
  >({});
  const [regionEdits, setRegionEdits] = useState<
    Record<string, QuestionRegionEdit[]>
  >({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const [p, q, bindings, matches, assignment] = await Promise.all([
        assignmentGenerationApi.listPageOrganization(revision.id),
        assignmentGenerationApi.listQuestionCandidates(revision.id),
        assignmentGenerationApi.listReferenceAnswerBindings(revision.id),
        assignmentGenerationApi.listTextbookSourceMatches(revision.id),
        assignmentsApi.get(revision.assignment_id),
      ]);
      setPages(p);
      setRegionPages(
        assignment.paper_version?.pages.map((page) => ({
          paper_page_id: page.id,
          current_page_number: page.page_number,
          current_status: page.status,
        })) ?? [],
      );
      setQuestions(q);
      setReferenceBindings(bindings);
      setTextbookMatches(matches);
      setBindingQuestions((old) => {
        const next = { ...old };
        for (const binding of bindings)
          next[binding.id] ??= binding.question_id ?? "";
        return next;
      });
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
      setRegionEdits((old) => {
        const next = { ...old };
        for (const item of q)
          next[item.id] ??= (item.regions ?? []).map((region) => ({
            paper_page_id: region.paper_page_id,
            x: String(region.x),
            y: String(region.y),
            width: String(region.width),
            height: String(region.height),
          }));
        return next;
      });
      setError("");
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "无法加载页面与题目候选",
      );
    }
  }, [revision.assignment_id, revision.id]);
  useEffect(() => {
    void load();
  }, [load]);
  const currentQuestions = questions.filter(
    (item) => !["superseded", "stale", "rejected"].includes(item.status),
  );
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
  async function saveRegions(item: QuestionExtractionCandidate) {
    setBusy(true);
    try {
      const regions = regionEdits[item.id] ?? [];
      await assignmentGenerationApi.updateQuestionRegions(item.id, {
        expected_teacher_edit_version: item.teacher_edit_version,
        expected_paper_version_id: item.paper_version_id,
        ...guard,
        regions: regions.map((region) => ({
          paper_page_id: region.paper_page_id,
          x: Number(region.x),
          y: Number(region.y),
          width: Number(region.width),
          height: Number(region.height),
        })),
      });
      onChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? `${reason.message}，请刷新后重试`
          : "题目区域保存失败",
      );
    } finally {
      setBusy(false);
    }
  }
  async function referenceBindingAction(
    item: ReferenceAnswerSourceBinding,
    action: "confirm" | "reject",
  ) {
    setBusy(true);
    try {
      await assignmentGenerationApi.dispositionReferenceAnswerBinding(item.id, {
        action,
        expected_edit_version: item.edit_version,
        expected_draft_revision_edit_version: revision.teacher_edit_version,
        expected_paper_version_id: item.paper_version_id,
        expected_source_snapshot: revision.source_snapshot_hash,
        ...(action === "confirm"
          ? {
              explicit_confirmation: true,
              question_id: bindingQuestions[item.id],
            }
          : {}),
      });
      onChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? `${reason.message}，请刷新后重试`
          : "参考答案来源绑定处理失败",
      );
    } finally {
      setBusy(false);
    }
  }
  async function extractReferenceCandidate(item: ReferenceAnswerSourceBinding) {
    setBusy(true);
    try {
      await assignmentGenerationApi.extractReferenceAnswerCandidate(item.id, {
        expected_binding_edit_version: item.edit_version,
        expected_draft_revision_edit_version: revision.teacher_edit_version,
        expected_source_snapshot: revision.source_snapshot_hash,
      });
      onChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? `${reason.message}，请刷新后重试`
          : "参考答案文本候选生成失败",
      );
    } finally {
      setBusy(false);
    }
  }
  async function textbookMatchAction(
    item: TextbookSourceMatch,
    action: "confirm" | "reject",
  ) {
    setBusy(true);
    try {
      await assignmentGenerationApi.dispositionTextbookSourceMatch(item.id, {
        action,
        expected_edit_version: item.edit_version,
        expected_draft_revision_edit_version: revision.teacher_edit_version,
        expected_paper_version_id: item.paper_version_id,
        expected_source_snapshot: revision.source_snapshot_hash,
        ...(action === "confirm" ? { explicit_confirmation: true } : {}),
      });
      onChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? `${reason.message}，请刷新后重试`
          : "教材出处候选处理失败",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="grid gap-2" aria-label="页面整理与题目抽取复核">
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <details className="border-t pt-1 open:pt-3">
        <summary className="cursor-pointer rounded-lg py-2 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
          页面核对（{pages.length} 页）
        </summary>
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
                <summary>查看识别依据</summary>
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
      {referenceBindings.length > 0 && (
        <details className="rounded-lg border border-cyan-200 bg-cyan-50/40 p-3">
          <summary className="cursor-pointer font-semibold">
            参考答案来源绑定（{referenceBindings.length}）
          </summary>
          <p className="mt-2 text-xs text-slate-600">
            先确认参考答案 PDF
            区域属于哪道题；确认后可显式生成可编辑文本候选，候选仍需教师复核，不接受答案、不生成评分。
          </p>
          <div className="mt-2 grid gap-2">
            {referenceBindings.map((binding) => (
              <article
                key={binding.id}
                className="rounded border bg-white p-3 text-sm"
              >
                <p className="font-medium">
                  检测题号 {binding.detected_number} ·{" "}
                  {binding.source_file_name ?? "参考答案文件"}
                </p>
                <p>
                  {binding.regions.length} 个区域 /{" "}
                  {new Set(binding.regions.map((x) => x.paper_page_id)).size} 页
                  · 置信度 {(binding.confidence * 100).toFixed(0)}% · 状态{" "}
                  {binding.status}
                </p>
                <p>风险：{binding.warning_codes.join("、") || "无"}</p>
                {binding.status === "suggested" && (
                  <div className="mt-2 flex flex-wrap items-end gap-2">
                    <label>
                      绑定到题目
                      <select
                        aria-label={`参考答案${binding.detected_number}绑定题目`}
                        className="mt-1 block rounded border p-2"
                        value={bindingQuestions[binding.id] ?? ""}
                        onChange={(event) =>
                          setBindingQuestions((old) => ({
                            ...old,
                            [binding.id]: event.target.value,
                          }))
                        }
                      >
                        <option value="">请选择题目</option>
                        {currentQuestions
                          .filter(
                            (question) => question.materialized_question_id,
                          )
                          .map((question) => (
                            <option
                              key={question.materialized_question_id}
                              value={question.materialized_question_id}
                            >
                              第 {question.question_number} 题
                            </option>
                          ))}
                      </select>
                    </label>
                    <Button
                      disabled={busy || !bindingQuestions[binding.id]}
                      onClick={() =>
                        void referenceBindingAction(binding, "confirm")
                      }
                    >
                      明确确认绑定
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void referenceBindingAction(binding, "reject")
                      }
                    >
                      拒绝此绑定
                    </Button>
                  </div>
                )}
                {binding.status === "confirmed" && (
                  <div className="mt-2">
                    <Button
                      disabled={busy}
                      onClick={() => void extractReferenceCandidate(binding)}
                    >
                      生成可编辑答案候选
                    </Button>
                    <p className="mt-1 text-xs text-slate-600">
                      仅提取已确认区域中的 PDF 文本；不会确认答案或生成评分。
                    </p>
                  </div>
                )}
              </article>
            ))}
          </div>
        </details>
      )}
      {textbookMatches.length > 0 && (
        <details className="rounded-lg border border-violet-200 bg-violet-50/40 p-3">
          <summary className="cursor-pointer font-semibold">
            教材出处（已自动匹配 {textbookMatches.length} 题）
          </summary>
          <div className="mt-3 grid gap-2">
            {textbookMatches.map((match) => (
              <article
                key={match.id}
                className="rounded border bg-white p-3 text-sm"
              >
                <p className="font-medium">
                  {match.question_number
                    ? `第 ${match.question_number} 题`
                    : `解答 ${match.solution_number ?? "未编号"}`}
                  {match.status === "confirmed"
                    ? " · 已确认出处"
                    : " · 找到可能出处"}
                </p>
                <p className="mt-1">
                  {match.source_file_name ?? "教材"}
                  {match.exercise_label ? ` · ${match.exercise_label}` : ""}
                  {match.detected_number
                    ? ` · 第 ${match.detected_number} 题`
                    : ""}
                  {` · PDF 第 ${match.pdf_page_number} 页`}
                </p>
                {match.status === "suggested" && (
                  <div className="mt-2 flex gap-2">
                    <Button
                      disabled={busy}
                      onClick={() => void textbookMatchAction(match, "confirm")}
                    >
                      确认出处
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => void textbookMatchAction(match, "reject")}
                    >
                      不是这里
                    </Button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </details>
      )}
      <div>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">题目核对</h3>
          {currentQuestions.some((x) => x.server_eligible) && (
            <Button
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  const first = currentQuestions[0];
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
              确认全部可直接采用的题目
            </Button>
          )}
        </div>
        <div className="mt-2 grid gap-3">
          {currentQuestions.map((item, index) => (
            <details
              key={item.id}
              className="rounded-lg border open:bg-[var(--neutral-50)]"
            >
              <summary className="cursor-pointer rounded-lg px-3 py-2 text-sm font-medium hover:bg-[var(--neutral-100)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
                {item.question_number || `候选 ${index + 1}`} ·{" "}
                {item.max_score == null ? "分值待定" : `${item.max_score} 分`} ·{" "}
                {item.server_eligible ? "可直接确认" : "待核对"}
              </summary>
              <div className="grid gap-3 px-4 pb-4">
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
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
                  <label className="block text-sm">
                    分值
                    <input
                      aria-label={`候选${index + 1}分值`}
                      type="number"
                      min="0.01"
                      step="0.01"
                      placeholder="待填写"
                      className="mt-1 w-full rounded border p-2"
                      value={edits[item.id]?.score ?? ""}
                      onChange={(e) =>
                        setEdits({
                          ...edits,
                          [item.id]: {
                            ...edits[item.id],
                            score: e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                </div>
                <label className="block text-sm">
                  题干
                  <textarea
                    aria-label={`候选${index + 1}题干`}
                    rows={3}
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
                <details className="rounded-lg border border-dashed px-3 py-2 text-sm">
                  <summary className="cursor-pointer text-[var(--neutral-600)]">
                    识别说明
                  </summary>
                  <div className="mt-2 grid gap-1 text-xs text-[var(--neutral-600)]">
                    {item.max_score == null && (
                      <p>系统未识别到分值，请填写后确认。</p>
                    )}
                    {(item.quality_stats?.suspicious_character_count ?? 0) >
                      0 ||
                    item.warning_codes.includes(
                      "CHARACTER_ENCODING_CORRUPTION_DETECTED",
                    ) ? (
                      <p>文字可能损坏，请重新识别或人工录入。</p>
                    ) : null}
                    {item.quality_stats?.text_source === "rapidocr" &&
                      (item.quality_stats?.low_confidence_block_count ?? 0) >
                        0 && <p>扫描文字置信度较低，请对照原文核对。</p>}
                    {(item.quality_stats?.has_formula_region ||
                      item.warning_codes.includes(
                        "FORMULA_REVIEW_REQUIRED",
                      )) && <p>公式需要核对。</p>}
                    {item.manual_required && (
                      <p>此题需要教师核对后才能采用。</p>
                    )}
                    {!!item.knowledge_point_suggestions.length && (
                      <p>
                        知识点：{item.knowledge_point_suggestions.join("、")}
                      </p>
                    )}
                    <details>
                      <summary className="cursor-pointer">技术依据</summary>
                      <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap">
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
                  </div>
                </details>
                <details className="rounded-lg border bg-white p-3 text-sm">
                  <summary className="cursor-pointer font-medium">
                    调整题目位置（{regionEdits[item.id]?.length ?? 0} 个区域）
                  </summary>
                  <p className="text-xs text-[var(--neutral-600)]">
                    加载页面预览后直接框选；保存位置不代表确认题目。
                  </p>
                  <QuestionRegionVisualEditor
                    assignmentId={revision.assignment_id}
                    pages={regionPages}
                    regions={regionEdits[item.id] ?? []}
                    questionLabel={`候选${index + 1}`}
                    disabled={busy}
                    onChange={(regions) =>
                      setRegionEdits((old) => ({
                        ...old,
                        [item.id]: regions,
                      }))
                    }
                  />
                  <details className="mt-3 rounded-lg border border-dashed p-2">
                    <summary className="cursor-pointer text-xs text-[var(--neutral-600)]">
                      高级坐标调整
                    </summary>
                    <div className="mt-2 grid gap-2">
                      {(regionEdits[item.id] ?? []).map(
                        (region, regionIndex) => (
                          <div
                            key={`${item.id}-${regionIndex}`}
                            className="grid gap-2 rounded border p-2 sm:grid-cols-6"
                          >
                            <label className="sm:col-span-2">
                              页面
                              <select
                                aria-label={`候选${index + 1}区域${regionIndex + 1}页面`}
                                className="mt-1 w-full rounded border p-2"
                                value={region.paper_page_id}
                                onChange={(event) =>
                                  setRegionEdits((old) => ({
                                    ...old,
                                    [item.id]: (old[item.id] ?? []).map(
                                      (value, position) =>
                                        position === regionIndex
                                          ? {
                                              ...value,
                                              paper_page_id: event.target.value,
                                            }
                                          : value,
                                    ),
                                  }))
                                }
                              >
                                {regionPages.map((page) => (
                                  <option
                                    key={page.paper_page_id}
                                    value={page.paper_page_id}
                                  >
                                    第 {page.current_page_number} 页
                                  </option>
                                ))}
                              </select>
                            </label>
                            {(["x", "y", "width", "height"] as const).map(
                              (field) => (
                                <label key={field}>
                                  {field}
                                  <input
                                    aria-label={`候选${index + 1}区域${regionIndex + 1}${field}`}
                                    type="number"
                                    min={
                                      field === "width" || field === "height"
                                        ? "0.001"
                                        : "0"
                                    }
                                    max="1"
                                    step="0.001"
                                    className="mt-1 w-full rounded border p-2"
                                    value={region[field]}
                                    onChange={(event) =>
                                      setRegionEdits((old) => ({
                                        ...old,
                                        [item.id]: (old[item.id] ?? []).map(
                                          (value, position) =>
                                            position === regionIndex
                                              ? {
                                                  ...value,
                                                  [field]: event.target.value,
                                                }
                                              : value,
                                        ),
                                      }))
                                    }
                                  />
                                </label>
                              ),
                            )}
                            <Button
                              variant="outline"
                              disabled={
                                busy || (regionEdits[item.id]?.length ?? 0) <= 1
                              }
                              onClick={() =>
                                setRegionEdits((old) => ({
                                  ...old,
                                  [item.id]: (old[item.id] ?? []).filter(
                                    (_value, position) =>
                                      position !== regionIndex,
                                  ),
                                }))
                              }
                            >
                              移除区域
                            </Button>
                          </div>
                        ),
                      )}
                    </div>
                  </details>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      disabled={
                        busy || (regionEdits[item.id]?.length ?? 0) === 0
                      }
                      onClick={() => void saveRegions(item)}
                    >
                      保存区域
                    </Button>
                  </div>
                </details>
                {item.status === "suggested" && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      disabled={busy}
                      onClick={() => void questionAction(item, "accept")}
                    >
                      确认题目
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() => void questionAction(item, "modify")}
                    >
                      保存修改并确认
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => void questionAction(item, "reject")}
                    >
                      不采用
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

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, Card, Select, useToast } from "@/components/ui";
import {
  ApiError,
  assignmentGenerationApi,
  type AnswerDraftCandidate,
  type AssignmentDraftRevision,
  type RubricDraftCandidate,
  type RubricDraftValidation,
} from "@/lib/api";

const sourceLabels: Record<string, string> = {
  teacher_official: "教师官方",
  publisher_official: "出版方官方",
  teacher_provided: "教师提供",
  third_party: "第三方",
  ai_generated: "AI 生成",
  unknown: "未知",
};

const validationLabels: Record<string, string> = {
  verified: "结构与确定性检查通过",
  partially_verified: "部分验证",
  indeterminate: "无法确定（不是已验证）",
  unsupported: "不支持，需人工处理",
  failed: "验证失败，仅作为风险",
  stale: "结果已失效",
};

type QuestionOption = {
  id: string;
  question_number: string;
  content_text?: string | null;
  max_score?: number | string | null;
};

function safeJson(value: string, label: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object")
      throw new Error();
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
}

export function AnswerRubricGenerationReview({
  assignmentId,
  questions,
}: {
  assignmentId: string;
  questions: QuestionOption[];
}) {
  const toast = useToast();
  const [revision, setRevision] = useState<AssignmentDraftRevision | null>(
    null,
  );
  const [answers, setAnswers] = useState<AnswerDraftCandidate[]>([]);
  const [rubrics, setRubrics] = useState<RubricDraftCandidate[]>([]);
  const [selectedQuestion, setSelectedQuestion] = useState(
    questions[0]?.id ?? "",
  );
  const [validations, setValidations] = useState<RubricDraftValidation[]>([]);
  const [answerText, setAnswerText] = useState("");
  const [alternativeText, setAlternativeText] = useState("");
  const [rubricTitle, setRubricTitle] = useState("");
  const [scoringMode, setScoringMode] =
    useState<RubricDraftCandidate["scoring_mode"]>("manual_only");
  const [domainJson, setDomainJson] = useState("{}");
  const [validationJson, setValidationJson] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const revisions =
        await assignmentGenerationApi.listRevisions(assignmentId);
      const current = revisions[0] ?? null;
      setRevision(current);
      if (!current) {
        setAnswers([]);
        setRubrics([]);
        return;
      }
      const [nextAnswers, nextRubrics] = await Promise.all([
        assignmentGenerationApi.listAnswerCandidates(current.id),
        assignmentGenerationApi.listRubricCandidates(current.id),
      ]);
      setAnswers(nextAnswers);
      setRubrics(nextRubrics);
      setMessage("");
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : "无法恢复答案与 Rubric 草稿",
      );
    }
  }, [assignmentId]);

  useEffect(() => void load(), [load]);
  const answer = useMemo(
    () => answers.find((item) => item.question_id === selectedQuestion),
    [answers, selectedQuestion],
  );
  const rubric = useMemo(
    () => rubrics.find((item) => item.question_id === selectedQuestion),
    [rubrics, selectedQuestion],
  );

  useEffect(() => {
    setAnswerText(answer?.raw_content ?? "");
    setAlternativeText(
      (answer?.alternative_answers ?? [])
        .map((item) => String(item.content ?? ""))
        .filter(Boolean)
        .join("\n"),
    );
  }, [answer]);
  useEffect(() => {
    setRubricTitle(rubric?.title ?? "");
    setScoringMode(rubric?.scoring_mode ?? "manual_only");
    setDomainJson(JSON.stringify(rubric?.domain_requirements ?? {}, null, 2));
    setValidationJson(JSON.stringify(rubric?.validation_config ?? {}, null, 2));
    if (!rubric) {
      setValidations([]);
      return;
    }
    void assignmentGenerationApi
      .rubricCandidateValidation(rubric.id)
      .then(setValidations)
      .catch(() => setValidations([]));
  }, [rubric]);

  const expected = (candidate: AnswerDraftCandidate | RubricDraftCandidate) => {
    if (!revision) throw new Error("没有可审查的生成草稿");
    return {
      expected_teacher_edit_version: candidate.teacher_edit_version,
      expected_draft_revision_edit_version: revision.teacher_edit_version,
      expected_question_version: candidate.question_version,
      expected_source_snapshot: revision.source_snapshot_hash,
    };
  };

  const perform = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try {
      await work();
      await load();
      toast(success);
    } catch (error) {
      const text =
        error instanceof ApiError && error.status === 409
          ? `并发冲突：${error.message}，请刷新后重试`
          : error instanceof Error
            ? error.message
            : "操作失败";
      setMessage(text);
      toast(text, "error");
    } finally {
      setBusy(false);
    }
  };

  if (!revision)
    return (
      <Card className="p-4" data-testid="answer-rubric-empty">
        尚无第四部分生成草稿。请先运行生成任务并确认题目；系统不会伪造答案。
      </Card>
    );

  return (
    <section
      className="space-y-4"
      aria-label="标准答案与 Structured Rubric 草稿"
    >
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm">
        AI/第三方答案不等于官方答案；接受只会物化未确认的 draft。indeterminate
        不是 verified，系统不会自动发布或写最终分数。
      </div>
      {message && (
        <p
          role="alert"
          className="rounded-xl border border-amber-300 bg-amber-50 p-3"
        >
          {message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void perform(
              () =>
                assignmentGenerationApi.acceptEligibleAnswers(revision.id, {
                  expected_draft_revision_edit_version:
                    revision.teacher_edit_version,
                  expected_source_snapshot: revision.source_snapshot_hash,
                }),
              "服务器判定的低风险答案已接受；来源标签保持不变",
            )
          }
        >
          批量接受 eligible 答案
        </Button>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void perform(
              () =>
                assignmentGenerationApi.acceptEligibleRubrics(revision.id, {
                  expected_draft_revision_edit_version:
                    revision.teacher_edit_version,
                  expected_source_snapshot: revision.source_snapshot_hash,
                }),
              "服务器判定的低风险 Rubric 已物化为 draft",
            )
          }
        >
          批量接受 eligible Rubric
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => void load()}>
          刷新草稿
        </Button>
      </div>
      <div className="grid gap-4 xl:grid-cols-[15rem_minmax(0,1fr)_minmax(0,1.2fr)]">
        <Card className="space-y-2 p-4">
          <h3 className="font-bold">题目与风险</h3>
          {questions.map((question) => {
            const a = answers.find((item) => item.question_id === question.id);
            const r = rubrics.find((item) => item.question_id === question.id);
            return (
              <button
                key={question.id}
                className={`w-full rounded-lg border p-3 text-left ${selectedQuestion === question.id ? "border-blue-600 bg-blue-50" : ""}`}
                onClick={() => setSelectedQuestion(question.id)}
                aria-pressed={selectedQuestion === question.id}
              >
                <span className="font-semibold">
                  第 {question.question_number} 题
                </span>
                <span className="block text-xs">
                  答案：{a?.status ?? "无草稿"}
                </span>
                <span className="block text-xs">
                  Rubric：{r?.status ?? "无草稿"}
                </span>
                <span className="block text-xs">
                  模式：{r?.scoring_mode ?? "—"}
                </span>
                {(a?.manual_required || r?.manual_required) && (
                  <span className="block text-xs text-amber-700">
                    需要人工处理
                  </span>
                )}
              </button>
            );
          })}
        </Card>

        <Card className="space-y-3 p-4" data-testid="answer-candidate-panel">
          <h3 className="font-bold">标准答案草稿与证据</h3>
          {!answer ? (
            <p>Provider unavailable 或题目尚未确认；没有伪造答案。</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-2 text-sm">
                <span className="rounded-full bg-slate-100 px-3 py-1">
                  来源：{sourceLabels[answer.source_type] ?? answer.source_type}
                </span>
                <span>confidence {answer.confidence.toFixed(2)}</span>
                <span>{answer.status}</span>
              </div>
              {!["teacher_official", "publisher_official"].includes(
                answer.source_type,
              ) && (
                <p className="text-sm text-amber-700">
                  此答案不显示为官方答案。
                </p>
              )}
              <label className="grid gap-1 text-sm font-medium">
                标准答案（纯文本安全渲染）
                <textarea
                  className="min-h-32 rounded-lg border p-2"
                  value={answerText}
                  onChange={(event) => setAnswerText(event.target.value)}
                />
              </label>
              <label className="grid gap-1 text-sm font-medium">
                替代答案（每行一个；未验证等价性时保持 indeterminate）
                <textarea
                  className="min-h-20 rounded-lg border p-2"
                  value={alternativeText}
                  onChange={(event) => setAlternativeText(event.target.value)}
                />
              </label>
              <details>
                <summary>provenance / evidence / warning codes</summary>
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">
                  {JSON.stringify(
                    {
                      provenance: answer.provenance,
                      evidence: answer.evidence,
                      warning_codes: answer.warning_codes,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionAnswerCandidate(
                          answer.id,
                          { action: "accept", ...expected(answer) },
                        ),
                      "答案已接受并物化为未确认 draft",
                    )
                  }
                >
                  接受答案
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionAnswerCandidate(
                          answer.id,
                          {
                            action: "modify",
                            ...expected(answer),
                            teacher_value: {
                              raw_content: answerText,
                              normalized_content: answerText,
                              structured_content: answer.structured_content,
                              alternative_answers: alternativeText
                                .split("\n")
                                .map((line) => line.trim())
                                .filter(Boolean)
                                .map((content) => ({
                                  content,
                                  relation: "candidate",
                                  equivalence_status: "indeterminate",
                                })),
                            },
                          },
                        ),
                      "教师修改已保存并物化为未确认 draft",
                    )
                  }
                >
                  修改后接受
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionAnswerCandidate(
                          answer.id,
                          { action: "reject", ...expected(answer) },
                        ),
                      "答案候选已拒绝",
                    )
                  }
                >
                  拒绝
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionAnswerCandidate(
                          answer.id,
                          {
                            action: "mark_manual_required",
                            ...expected(answer),
                          },
                        ),
                      "已标记人工答案",
                    )
                  }
                >
                  标记人工答案
                </Button>
              </div>
            </>
          )}
        </Card>

        <Card className="space-y-3 p-4" data-testid="rubric-candidate-panel">
          <h3 className="font-bold">Structured Rubric 草稿</h3>
          {!rubric ? (
            <p>暂无 Rubric 候选。</p>
          ) : (
            <>
              <label className="grid gap-1 text-sm font-medium">
                Rubric 标题
                <input
                  className="rounded-lg border p-2"
                  value={rubricTitle}
                  onChange={(event) => setRubricTitle(event.target.value)}
                />
              </label>
              <Select
                label="评分模式"
                value={scoringMode}
                onChange={(event) =>
                  setScoringMode(
                    event.target.value as RubricDraftCandidate["scoring_mode"],
                  )
                }
              >
                <option value="deterministic">deterministic</option>
                <option value="ai_suggestion">ai_suggestion</option>
                <option value="hybrid">hybrid</option>
                <option value="manual_only">manual_only</option>
              </Select>
              <p className="text-sm">
                总分：{rubric.total_points ?? "未知（阻止确认）"} · confidence{" "}
                {rubric.confidence.toFixed(2)}
              </p>
              <div className="space-y-2" aria-label="Rubric 评分项">
                {rubric.criteria.map((criterion) => (
                  <div
                    key={criterion.id}
                    className="rounded-lg border p-3 text-sm"
                  >
                    <strong>
                      {criterion.criterion_key} · {criterion.title}
                    </strong>
                    <p>
                      分值 {criterion.points ?? "未知"} ·{" "}
                      {criterion.criterion_type} ·{" "}
                      {criterion.required ? "必要" : "可选"}
                    </p>
                    <p>
                      依赖：{criterion.dependency_keys.join(", ") || "无"} ·
                      替代路径：{criterion.alternative_group ?? "无"}
                    </p>
                    <p>
                      部分分：{JSON.stringify(criterion.partial_credit_rule)} ·
                      扣分：{JSON.stringify(criterion.deduction_rule)}
                    </p>
                    <p>
                      常见错误：
                      {criterion.common_error_codes.join(", ") || "无"}
                    </p>
                    <p>反馈模板：{criterion.feedback_template ?? "无"}</p>
                  </div>
                ))}
              </div>
              <label className="grid gap-1 text-sm font-medium">
                数域/单位/精度/格式要求 JSON
                <textarea
                  className="min-h-24 rounded-lg border p-2 font-mono text-xs"
                  value={domainJson}
                  onChange={(event) => setDomainJson(event.target.value)}
                />
              </label>
              <label className="grid gap-1 text-sm font-medium">
                确定性验证配置 JSON
                <textarea
                  className="min-h-24 rounded-lg border p-2 font-mono text-xs"
                  value={validationJson}
                  onChange={(event) => setValidationJson(event.target.value)}
                />
              </label>
              <div
                aria-label="数学验证结果"
                className="space-y-1 rounded-lg bg-slate-50 p-3 text-sm"
              >
                {validations.length ? (
                  validations.map((result) => (
                    <p key={result.id}>
                      <strong>
                        {validationLabels[result.status] ?? result.status}
                      </strong>{" "}
                      · {result.issue_codes.join(", ")}
                    </p>
                  ))
                ) : (
                  <p>尚无验证结果。</p>
                )}
              </div>
              <details>
                <summary>
                  evidence / common errors / feedback templates / issues
                </summary>
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">
                  {JSON.stringify(
                    {
                      evidence: rubric.evidence,
                      common_error_types: rubric.common_error_types,
                      feedback_templates: rubric.feedback_templates,
                      warnings: rubric.warning_codes,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionRubricCandidate(
                          rubric.id,
                          { action: "accept", ...expected(rubric) },
                        ),
                      "Rubric 已物化为未确认 draft",
                    )
                  }
                >
                  接受 Rubric
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionRubricCandidate(
                          rubric.id,
                          {
                            action: "modify",
                            ...expected(rubric),
                            teacher_value: {
                              title: rubricTitle,
                              scoring_mode: scoringMode,
                              total_points: rubric.total_points
                                ? Number(rubric.total_points)
                                : undefined,
                              allow_partial_credit: rubric.allow_partial_credit,
                              domain_requirements: safeJson(
                                domainJson,
                                "数域要求",
                              ),
                              validation_config: safeJson(
                                validationJson,
                                "验证配置",
                              ),
                              common_error_types: rubric.common_error_types,
                              feedback_templates: rubric.feedback_templates,
                              criteria: rubric.criteria.map((criterion) => ({
                                criterion_key: criterion.criterion_key,
                                title: criterion.title,
                                description: criterion.description,
                                points: criterion.points,
                                criterion_type: criterion.criterion_type,
                                required: criterion.required,
                                dependency_keys: criterion.dependency_keys,
                                alternative_group: criterion.alternative_group,
                                partial_credit_rule:
                                  criterion.partial_credit_rule,
                                deduction_rule: criterion.deduction_rule,
                                validation_rule: criterion.validation_rule,
                                common_error_codes:
                                  criterion.common_error_codes,
                                feedback_template: criterion.feedback_template,
                                confidence: criterion.confidence,
                                evidence: criterion.evidence,
                                manual_required: criterion.manual_required,
                              })),
                            },
                          },
                        ),
                      "Rubric 教师修改已物化为未确认 draft",
                    )
                  }
                >
                  修改后接受
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionRubricCandidate(
                          rubric.id,
                          { action: "reject", ...expected(rubric) },
                        ),
                      "Rubric 候选已拒绝",
                    )
                  }
                >
                  拒绝
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        assignmentGenerationApi.dispositionRubricCandidate(
                          rubric.id,
                          { action: "mark_manual_only", ...expected(rubric) },
                        ),
                      "已标记 manual_only",
                    )
                  }
                >
                  标记 manual_only
                </Button>
              </div>
            </>
          )}
        </Card>
      </div>
    </section>
  );
}

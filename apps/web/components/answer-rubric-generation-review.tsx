"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button, Card, Select, useToast } from "@/components/ui";
import {
  ApiError,
  assignmentGenerationApi,
  assignmentReviewApi,
  structuredRubricApi,
  type AnswerDraftCandidate,
  type AssignmentDraftRevision,
  type AssignmentReviewBundle,
  type BulkCandidateAcceptance,
  type RubricDraftCandidate,
  type RubricDraftValidation,
} from "@/lib/api";

const validationLabels: Record<string, string> = {
  verified: "结构与确定性检查通过",
  partially_verified: "部分验证",
  indeterminate: "无法确定（不是已验证）",
  unsupported: "不支持，需人工处理",
  failed: "验证失败，仅作为风险",
  stale: "结果已失效",
};

const eligibilityReasonLabels: Record<string, string> = {
  CANDIDATE_NOT_SUGGESTED: "这项建议已经处理或已失效",
  ANSWER_SOURCE_UNKNOWN: "答案来源尚未确定",
  ANSWER_CONFIDENCE_LOW: "答案置信度不足 0.80",
  ANSWER_EVIDENCE_MISSING: "答案缺少可核对证据",
  ANSWER_CANDIDATE_MISSING: "缺少对应参考答案候选",
  ANSWER_CANDIDATE_NOT_ACCEPTED: "请先接受对应参考答案",
  MANUAL_REVIEW_REQUIRED: "系统判定需要教师人工核对",
  SCORING_MODE_NOT_DETERMINISTIC: "评分模式不是可自动接受的确定性模式",
  VALIDATION_INDETERMINATE: "数学校验结果仍无法确定",
  RUBRIC_SCHEMA_INVALID: "评分项结构不完整",
  RUBRIC_DEPENDENCY_MISSING: "评分项引用了不存在的前置项",
  RUBRIC_DEPENDENCY_CYCLE: "评分项之间存在循环依赖",
  RUBRIC_PARTIAL_CREDIT_INVALID: "部分得分规则超过该项分值",
  RUBRIC_DEDUCTION_INVALID: "扣分规则超过该项分值",
  RUBRIC_VALIDATION_CONFIG_INVALID: "确定性校验规则不完整",
  RUBRIC_SCORE_REQUIRED: "评分标准缺少总分",
  RUBRIC_POINTS_MISMATCH: "评分项分值合计与题目总分不一致",
  RUBRIC_ALTERNATIVE_PATH_CONFLICT: "可选得分路径的分值存在冲突",
  PROMPT_INJECTION_CONTENT_DETECTED: "内容含有可疑指令，需要人工核对",
  FORMULA_ANSWER_REVIEW_REQUIRED: "公式答案需要人工核对",
  MANUAL_ANSWER_REQUIRED: "答案需要人工填写",
  ANSWER_SCHEMA_INVALID: "答案结构不完整",
};

const eligibilityReason = (code: string) =>
  eligibilityReasonLabels[code] ?? `需要人工核对（${code}）`;

type QuestionOption = {
  id: string;
  question_number: string;
  content_text?: string | null;
  max_score?: number | string | null;
};

type SavedRubricOption = {
  id: string;
  question_id: string;
  standard_answer?: string;
  scoring_notes?: string;
  items: Array<{
    id: string;
    title: string;
    description?: string | null;
    points: string;
    required: boolean;
    deduction_rule?: string | null;
  }>;
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
  savedRubrics = [],
}: {
  assignmentId: string;
  questions: QuestionOption[];
  savedRubrics?: SavedRubricOption[];
}) {
  const toast = useToast();
  const [revision, setRevision] = useState<AssignmentDraftRevision | null>(
    null,
  );
  const [answers, setAnswers] = useState<AnswerDraftCandidate[]>([]);
  const [rubrics, setRubrics] = useState<RubricDraftCandidate[]>([]);
  const [bundle, setBundle] = useState<AssignmentReviewBundle>();
  const [bundleError, setBundleError] = useState("");
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
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    try {
      const [revisions, nextBundle] = await Promise.all([
        assignmentGenerationApi.listRevisions(assignmentId),
        assignmentReviewApi.bundle(assignmentId),
      ]);
      if (generation !== loadGeneration.current) return;
      setBundle(nextBundle);
      setBundleError("");
      const current = revisions[0] ?? null;
      setRevision(current);
      if (!current) {
        setAnswers([]);
        setRubrics([]);
        setMessage("");
        return;
      }
      const [nextAnswers, nextRubrics] = await Promise.all([
        assignmentGenerationApi.listAnswerCandidates(current.id),
        assignmentGenerationApi.listRubricCandidates(current.id),
      ]);
      if (generation !== loadGeneration.current) return;
      setAnswers(nextAnswers);
      setRubrics(nextRubrics);
      setMessage("");
    } catch (error) {
      if (generation !== loadGeneration.current) return;
      setBundle(undefined);
      setRevision(null);
      setAnswers([]);
      setRubrics([]);
      setValidations([]);
      setBundleError("无法取得当前审查内容，请重试后再确认。");
      setMessage(
        error instanceof ApiError
          ? error.message
          : "无法恢复答案与 Rubric 草稿",
      );
    }
  }, [assignmentId]);

  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current += 1;
    };
  }, [load]);
  const answer = useMemo(
    () => answers.find((item) => item.question_id === selectedQuestion),
    [answers, selectedQuestion],
  );
  const rubric = useMemo(
    () => rubrics.find((item) => item.question_id === selectedQuestion),
    [rubrics, selectedQuestion],
  );
  const savedRubric = useMemo(
    () => savedRubrics.find((item) => item.question_id === selectedQuestion),
    [savedRubrics, selectedQuestion],
  );
  const bundleQuestion = bundle?.questions.find(
    (question) => question.id === selectedQuestion,
  );
  const answerSuggestion = bundleQuestion
    ? answers.find((item) => item.id === bundleQuestion.answer.candidate?.id)
    : answer;
  const rubricSuggestion = bundleQuestion
    ? rubrics.find((item) => item.id === bundleQuestion.rubric.candidate?.id)
    : rubric;
  const formalAnswer =
    bundleQuestion?.answer.selected ??
    (bundleQuestion?.answer.materialized?.status === "draft"
      ? bundleQuestion.answer.materialized
      : null);
  const formalRubric =
    bundleQuestion?.rubric.selected ??
    (bundleQuestion?.rubric.materialized?.status === "draft"
      ? bundleQuestion.rubric.materialized
      : null);
  const pendingAnswer =
    bundleQuestion?.answer.materialized?.status === "draft" &&
    bundleQuestion.answer.materialized.id !== formalAnswer?.id
      ? bundleQuestion.answer.materialized
      : null;
  const pendingRubric =
    bundleQuestion?.rubric.materialized?.status === "draft" &&
    bundleQuestion.rubric.materialized.id !== formalRubric?.id
      ? bundleQuestion.rubric.materialized
      : null;
  const displayQuestions = bundleError
    ? []
    : questions.length
      ? questions
      : (bundle?.questions ?? []);

  useEffect(() => {
    if (
      bundle?.questions.length &&
      !bundle.questions.some((question) => question.id === selectedQuestion)
    ) {
      setSelectedQuestion(bundle.questions[0].id);
    }
  }, [bundle, selectedQuestion]);

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

  const performBulk = async (
    label: "答案" | "评分标准",
    work: () => Promise<BulkCandidateAcceptance>,
  ) => {
    setBusy(true);
    try {
      const result = await work();
      await load();
      const diagnosticsAvailable =
        result.considered_count !== undefined &&
        result.skipped_count !== undefined &&
        result.skipped !== undefined;
      if (!diagnosticsAvailable && result.accepted_count === 0) {
        const text = `没有接受任何${label}，但服务端未返回跳过原因；请刷新页面后重试`;
        setMessage(text);
        toast(text, "error");
        return;
      }
      const consideredCount = result.considered_count ?? result.accepted_count;
      const skipped = result.skipped ?? [];
      const skippedCount = result.skipped_count ?? skipped.length;
      if (consideredCount === 0) {
        const text = `没有待处理的${label}建议`;
        setMessage("");
        toast(text);
        return;
      }
      const details = skipped
        .slice(0, 5)
        .map((item) => {
          const question = questions.find(
            (entry) => entry.id === item.question_id,
          );
          const prefix = question
            ? `第 ${question.question_number} 题`
            : "未识别题目";
          return `${prefix}：${item.reason_codes.map(eligibilityReason).join("、")}`;
        })
        .join("；");
      const omitted = Math.max(skippedCount - 5, 0);
      const skippedText = skippedCount
        ? `；${skippedCount} 项不能自动接受：${details}${omitted ? `；另有 ${omitted} 项` : ""}`
        : "";
      const text =
        result.accepted_count > 0
          ? `已批量接受 ${result.accepted_count} 项${label}${skippedText}`
          : `没有可自动接受的${label}${skippedText}`;
      setMessage(skippedCount ? text : "");
      toast(text, result.accepted_count === 0 ? "error" : "success");
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

  if (!revision && !bundle?.questions.length)
    return (
      <Card className="space-y-3 p-4" data-testid="answer-rubric-empty">
        <p>
          {bundleError ||
            "尚无第四部分生成草稿。请先运行生成任务并确认题目；系统不会伪造答案。"}
        </p>
        {bundleError && <Button onClick={() => void load()}>重试</Button>}
      </Card>
    );

  return (
    <section
      className="space-y-4"
      aria-label="标准答案与 Structured Rubric 草稿"
    >
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm">
        AI 或外部参考内容不等于教师确认答案；接受后仍需教师明确确认。
        无法确定的校验结果不会被当作已验证，系统不会自动发布或写入最终分数。
      </div>
      {message && (
        <p
          role="alert"
          className="rounded-xl border border-amber-300 bg-amber-50 p-3"
        >
          {message}
        </p>
      )}
      {bundleError && (
        <p
          role="alert"
          className="rounded-xl border border-red-300 bg-red-50 p-3"
        >
          {bundleError}
          <Button
            className="ml-3"
            variant="outline"
            onClick={() => void load()}
          >
            重试
          </Button>
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy || !revision}
          onClick={() =>
            revision &&
            void performBulk("答案", () =>
              assignmentGenerationApi.acceptEligibleAnswers(revision.id, {
                expected_draft_revision_edit_version:
                  revision.teacher_edit_version,
                expected_source_snapshot: revision.source_snapshot_hash,
              }),
            )
          }
        >
          批量接受可用答案
        </Button>
        <Button
          variant="outline"
          disabled={busy || !revision}
          onClick={() =>
            revision &&
            void performBulk("评分标准", () =>
              assignmentGenerationApi.acceptEligibleRubrics(revision.id, {
                expected_draft_revision_edit_version:
                  revision.teacher_edit_version,
                expected_source_snapshot: revision.source_snapshot_hash,
              }),
            )
          }
        >
          批量接受可用评分标准
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => void load()}>
          刷新草稿
        </Button>
      </div>
      <div className="grid gap-4 xl:grid-cols-[15rem_minmax(0,1fr)_minmax(0,1.2fr)]">
        <Card className="space-y-2 p-4">
          <h3 className="font-bold">题目与风险</h3>
          {displayQuestions.map((question) => {
            const questionNumber =
              "number" in question ? question.number : question.question_number;
            const savedQuestion = bundle?.questions.find(
              (entry) => entry.id === question.id,
            );
            const savedAnswer = savedQuestion?.answer.selected;
            const savedRubric = savedQuestion?.rubric.selected;
            const currentRubric = savedRubrics.find(
              (entry) => entry.question_id === question.id,
            );
            const riskCount =
              bundle?.blockers.filter(
                (blocker) => blocker.entity_id === question.id,
              ).length ?? 0;
            return (
              <button
                key={question.id}
                className={`w-full rounded-lg border p-3 text-left ${selectedQuestion === question.id ? "border-blue-600 bg-blue-50" : ""}`}
                onClick={() => setSelectedQuestion(question.id)}
                aria-pressed={selectedQuestion === question.id}
              >
                <span className="font-semibold">第 {questionNumber} 题</span>
                <span className="block text-xs">
                  参考答案：
                  {savedAnswer?.status === "confirmed"
                    ? "已确认"
                    : savedAnswer?.status === "draft"
                      ? "等待确认"
                      : currentRubric?.standard_answer
                        ? "已保存"
                        : savedQuestion?.answer.candidate
                          ? "有生成建议"
                          : "尚未生成"}
                </span>
                <span className="block text-xs">
                  评分标准：
                  {savedRubric?.status === "confirmed"
                    ? "已确认"
                    : savedRubric?.status === "draft"
                      ? "等待确认"
                      : currentRubric?.items.length
                        ? "已保存"
                        : savedQuestion?.rubric.candidate
                          ? "有生成建议"
                          : "尚未生成"}
                </span>
                {riskCount > 0 && (
                  <span className="mt-1 block text-xs text-amber-700">
                    {riskCount} 项风险待处理
                  </span>
                )}
              </button>
            );
          })}
        </Card>

        <Card className="space-y-3 p-4" data-testid="answer-candidate-panel">
          <h3 className="font-bold">标准答案草稿与证据</h3>
          {formalAnswer ? (
            <div className="space-y-3" data-testid="saved-reference-answer">
              <div className="flex flex-wrap gap-2 text-sm">
                <span>版本 {formalAnswer.version}</span>
                <span>
                  {formalAnswer.status === "confirmed" ? "已确认" : "待确认"}
                </span>
              </div>
              <div className="whitespace-pre-wrap rounded-lg border bg-slate-50 p-3 text-sm">
                {formalAnswer.content}
              </div>
              {formalAnswer.status === "draft" ? (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () =>
                        structuredRubricApi.confirmReference(formalAnswer.id),
                      "参考答案已确认",
                    )
                  }
                >
                  确认此参考答案
                </Button>
              ) : (
                <p className="text-sm text-emerald-700">
                  ✓ 此参考答案已经教师确认
                </p>
              )}
              {pendingAnswer && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
                  <p className="font-medium">有一份较新的参考答案等待确认</p>
                  <p className="mt-1 whitespace-pre-wrap">
                    {pendingAnswer.content}
                  </p>
                  <Button
                    className="mt-2"
                    disabled={busy}
                    onClick={() =>
                      void perform(
                        () =>
                          structuredRubricApi.confirmReference(
                            pendingAnswer.id,
                          ),
                        "参考答案已确认",
                      )
                    }
                  >
                    确认这份参考答案
                  </Button>
                </div>
              )}
              {bundleQuestion?.answer.history.some(
                (entry) => entry.status === "retired",
              ) && (
                <details className="rounded-lg border p-3 text-sm">
                  <summary className="cursor-pointer">查看历史参考答案</summary>
                  <ul className="mt-2 space-y-2">
                    {bundleQuestion?.answer.history
                      .filter((entry) => entry.status === "retired")
                      .map((entry) => (
                        <li key={entry.id} className="rounded bg-slate-50 p-2">
                          {entry.content}
                        </li>
                      ))}
                  </ul>
                </details>
              )}
              {answerSuggestion && (
                <details className="rounded-lg border p-3 text-sm">
                  <summary className="cursor-pointer">
                    查看生成建议并处理
                  </summary>
                  <p className="mt-2 whitespace-pre-wrap">
                    {answerSuggestion.raw_content}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void perform(
                          () =>
                            assignmentGenerationApi.dispositionAnswerCandidate(
                              answerSuggestion.id,
                              {
                                action: "accept",
                                ...expected(answerSuggestion),
                              },
                            ),
                          "参考答案已保存，等待教师确认",
                        )
                      }
                    >
                      接受建议
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void perform(
                          () =>
                            assignmentGenerationApi.dispositionAnswerCandidate(
                              answerSuggestion.id,
                              {
                                action: "reject",
                                ...expected(answerSuggestion),
                              },
                            ),
                          "建议已拒绝",
                        )
                      }
                    >
                      拒绝建议
                    </Button>
                  </div>
                </details>
              )}
            </div>
          ) : savedRubric?.standard_answer ? (
            <div className="space-y-3" data-testid="saved-legacy-answer">
              <p className="text-sm text-emerald-700">✓ 已保存到作业草稿</p>
              <div className="whitespace-pre-wrap rounded-lg border bg-slate-50 p-3 text-sm">
                {savedRubric.standard_answer}
              </div>
              {savedRubric.scoring_notes && (
                <p className="text-sm text-slate-600">
                  {savedRubric.scoring_notes}
                </p>
              )}
            </div>
          ) : !answer ? (
            <p>Provider unavailable 或题目尚未确认；没有伪造答案。</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-2 text-sm">
                <span className="rounded-full bg-slate-100 px-3 py-1">
                  生成建议
                </span>
                <span>置信度 {answer.confidence.toFixed(2)}</span>
              </div>
              {answer.server_eligible === false &&
                (answer.ineligibility_reasons?.length ?? 0) > 0 && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
                    <p className="font-medium">此答案不能自动接受</p>
                    <ul className="mt-1 list-disc pl-5">
                      {answer.ineligibility_reasons?.map((code) => (
                        <li key={code}>{eligibilityReason(code)}</li>
                      ))}
                    </ul>
                    <p className="mt-1 text-slate-700">
                      请核对内容后使用“接受答案”或“修改后接受”。
                    </p>
                  </div>
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
                <summary>查看证据与技术详情</summary>
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
                      "答案已保存，等待教师确认",
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
                      "教师修改已保存，等待确认",
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
                      "答案建议已拒绝",
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
          <h3 className="font-bold">评分标准草稿</h3>
          {formalRubric ? (
            <div className="space-y-3" data-testid="saved-structured-rubric">
              <div>
                <p className="font-semibold">{formalRubric.title}</p>
                <p className="text-sm">
                  总分：{formalRubric.total_points} · 版本{" "}
                  {formalRubric.version} ·{" "}
                  {formalRubric.status === "confirmed"
                    ? "已确认"
                    : formalRubric.status === "draft"
                      ? "待确认"
                      : "已停用"}
                </p>
              </div>
              <div className="space-y-2" aria-label="正式 Rubric 评分项">
                {formalRubric.criteria.map((criterion) => (
                  <div
                    key={criterion.id}
                    className="rounded-lg border p-3 text-sm"
                  >
                    <strong>
                      {criterion.key} · {criterion.title}
                    </strong>
                    <p>
                      分值 {criterion.points} · {criterion.criterion_type} ·{" "}
                      {criterion.required ? "必要" : "可选"}
                    </p>
                    {criterion.description && (
                      <p className="mt-1 whitespace-pre-wrap text-slate-700">
                        {criterion.description}
                      </p>
                    )}
                    <p className="mt-1 text-xs text-slate-500">
                      验证方式：
                      {criterion.validation_mode === "deterministic"
                        ? "确定性核查"
                        : "教师人工核查"}
                    </p>
                  </div>
                ))}
              </div>
              {formalRubric.status === "draft" ? (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () => structuredRubricApi.confirm(formalRubric.id),
                      "Structured Rubric 已确认",
                    )
                  }
                >
                  确认此评分标准
                </Button>
              ) : formalRubric.status === "confirmed" ? (
                <p className="text-sm text-emerald-700">
                  ✓ 此评分标准已经教师确认
                </p>
              ) : null}
              {pendingRubric && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
                  <p className="font-medium">有一份较新的评分标准等待确认</p>
                  <p>
                    {pendingRubric.title} · 总分 {pendingRubric.total_points}
                  </p>
                  <Button
                    className="mt-2"
                    disabled={busy}
                    onClick={() =>
                      void perform(
                        () => structuredRubricApi.confirm(pendingRubric.id),
                        "评分标准已确认",
                      )
                    }
                  >
                    确认这份评分标准
                  </Button>
                </div>
              )}
              {bundleQuestion?.rubric.history.some(
                (entry) => entry.status === "retired",
              ) && (
                <details className="rounded-lg border p-3 text-sm">
                  <summary className="cursor-pointer">查看历史评分标准</summary>
                  <ul className="mt-2 space-y-2">
                    {bundleQuestion?.rubric.history
                      .filter((entry) => entry.status === "retired")
                      .map((entry) => (
                        <li key={entry.id} className="rounded bg-slate-50 p-2">
                          {entry.title}
                        </li>
                      ))}
                  </ul>
                </details>
              )}
              {rubricSuggestion && (
                <details className="rounded-lg border p-3 text-sm">
                  <summary className="cursor-pointer">
                    查看生成建议并处理
                  </summary>
                  <p className="mt-2">{rubricSuggestion.title}</p>
                  <Button
                    className="mt-2"
                    disabled={busy}
                    onClick={() =>
                      void perform(
                        () =>
                          assignmentGenerationApi.dispositionRubricCandidate(
                            rubricSuggestion.id,
                            { action: "accept", ...expected(rubricSuggestion) },
                          ),
                        "评分标准已保存，等待教师确认",
                      )
                    }
                  >
                    接受建议
                  </Button>
                </details>
              )}
            </div>
          ) : savedRubric ? (
            <div className="space-y-3" data-testid="saved-legacy-rubric">
              <p className="text-sm text-emerald-700">✓ 已保存到作业草稿</p>
              <div className="space-y-2" aria-label="已保存评分项">
                {savedRubric.items.map((item) => (
                  <div key={item.id} className="rounded-lg border p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <strong>{item.title}</strong>
                      <span>{item.points} 分</span>
                    </div>
                    {item.description && (
                      <p className="mt-1 text-slate-700">{item.description}</p>
                    )}
                    {item.deduction_rule && (
                      <p className="mt-1 text-xs text-slate-500">
                        {item.deduction_rule}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : !rubric ? (
            <p>暂无评分标准建议，也没有已生成的 Structured Rubric。</p>
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
              {rubric.server_eligible === false &&
                (rubric.ineligibility_reasons?.length ?? 0) > 0 && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
                    <p className="font-medium">此评分标准不能自动接受</p>
                    <ul className="mt-1 list-disc pl-5">
                      {rubric.ineligibility_reasons?.map((code) => (
                        <li key={code}>{eligibilityReason(code)}</li>
                      ))}
                    </ul>
                    <p className="mt-1 text-slate-700">
                      请补全校验规则，或核对后使用“修改后接受”。
                    </p>
                  </div>
                )}
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
                      "评分标准建议已拒绝",
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

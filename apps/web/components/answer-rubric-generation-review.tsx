"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button, Card, Select, useToast } from "@/components/ui";
import { RubricTemplateActions } from "@/components/rubric-template-actions";
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
  PROVIDER_OUTPUT_DEGRADED: "生成内容包含需要教师判断的部分",
};

const eligibilityReason = (code: string) =>
  eligibilityReasonLabels[code] ?? `需要人工核对（${code}）`;

function teacherEligibilityMessages(
  codes: string[] | undefined,
  status: string,
  kind: "answer" | "rubric",
) {
  const uniqueCodes = [...new Set(codes ?? [])];
  if (status !== "manual_required") return uniqueCodes.map(eligibilityReason);

  const genericManualCodes = new Set([
    "CANDIDATE_NOT_SUGGESTED",
    "MANUAL_REVIEW_REQUIRED",
    "PROVIDER_OUTPUT_DEGRADED",
    "SCORING_MODE_NOT_DETERMINISTIC",
  ]);
  const details = uniqueCodes
    .filter((code) => !genericManualCodes.has(code))
    .map(eligibilityReason);
  return [
    kind === "answer"
      ? "请核对答案内容、推导过程和部分分要求"
      : "请核对评分项、分值和部分分规则",
    ...details,
  ];
}

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
  const [regeneratingQuestionId, setRegeneratingQuestionId] = useState("");
  const [message, setMessage] = useState("");
  const loadGeneration = useRef(0);
  const actionInFlight = useRef(false);

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
  const answerEligibilityMessages = teacherEligibilityMessages(
    answer?.ineligibility_reasons,
    answer?.status ?? "",
    "answer",
  );
  const rubricEligibilityMessages = teacherEligibilityMessages(
    rubric?.ineligibility_reasons,
    rubric?.status ?? "",
    "rubric",
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
  const hasLocalEdits =
    (!!answer &&
      (answerText !== (answer.raw_content ?? "") ||
        alternativeText !==
          (answer.alternative_answers ?? [])
            .map((entry) => String(entry.content ?? ""))
            .filter(Boolean)
            .join("\n"))) ||
    (!!rubric &&
      (rubricTitle !== rubric.title ||
        scoringMode !== rubric.scoring_mode ||
        domainJson !==
          JSON.stringify(rubric.domain_requirements ?? {}, null, 2) ||
        validationJson !==
          JSON.stringify(rubric.validation_config ?? {}, null, 2)));
  const allQuestionPackages = useMemo(() => {
    if (!bundle?.questions.length) return null;
    const packages = bundle.questions.map((question) => {
      const answerToConfirm =
        question.answer.materialized?.status === "draft"
          ? question.answer.materialized
          : question.answer.selected;
      const rubricToConfirm =
        question.rubric.materialized?.status === "draft"
          ? question.rubric.materialized
          : question.rubric.selected;
      if (
        !answerToConfirm ||
        !rubricToConfirm ||
        rubricToConfirm.reference_answer_version_id !== answerToConfirm.id
      )
        return null;
      return {
        question_id: question.id,
        expected_question_content_hash: question.content_hash,
        reference_answer_version_id: answerToConfirm.id,
        expected_reference_answer_content_hash: answerToConfirm.content_hash,
        structured_rubric_version_id: rubricToConfirm.id,
        expected_structured_rubric_content_hash: rubricToConfirm.content_hash,
        confirmed:
          answerToConfirm.status === "confirmed" &&
          rubricToConfirm.status === "confirmed",
      };
    });
    return packages.some((item) => item === null)
      ? null
      : packages.filter((item) => item !== null);
  }, [bundle]);
  const allQuestionPackagesConfirmed =
    !!allQuestionPackages?.length &&
    allQuestionPackages.every((item) => item.confirmed);
  const allCandidatePackages = useMemo(() => {
    if (!bundle?.questions.length || !revision) return null;
    const packages = bundle.questions.map((question) => {
      const answerCandidate = answers.find(
        (item) => item.id === question.answer.candidate?.id,
      );
      const rubricCandidate = rubrics.find(
        (item) => item.id === question.rubric.candidate?.id,
      );
      if (
        !answerCandidate ||
        !rubricCandidate ||
        rubricCandidate.answer_candidate_id !== answerCandidate.id
      )
        return null;
      return {
        question_id: question.id,
        expected_question_content_hash: question.content_hash,
        answer_candidate_id: answerCandidate.id,
        expected_answer_candidate_edit_version:
          answerCandidate.teacher_edit_version,
        expected_answer_question_version: answerCandidate.question_version,
        rubric_candidate_id: rubricCandidate.id,
        expected_rubric_candidate_edit_version:
          rubricCandidate.teacher_edit_version,
        expected_rubric_question_version: rubricCandidate.question_version,
      };
    });
    return packages.some((item) => item === null)
      ? null
      : packages.filter((item) => item !== null);
  }, [answers, bundle, revision, rubrics]);
  const canConfirmAll = !!allQuestionPackages || !!allCandidatePackages;
  const localDraftKey = `ahamark:answer-rubric-draft:${assignmentId}:${selectedQuestion}`;

  const saveLocalDraft = (changes: Record<string, unknown>) => {
    if (typeof window === "undefined" || !selectedQuestion) return;
    window.localStorage.setItem(
      localDraftKey,
      JSON.stringify({
        answerCandidateId: answer?.id ?? null,
        rubricCandidateId: rubric?.id ?? null,
        answerText,
        alternativeText,
        rubricTitle,
        scoringMode,
        domainJson,
        validationJson,
        savedAt: new Date().toISOString(),
        ...changes,
      }),
    );
  };

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

  useEffect(() => {
    if (typeof window === "undefined" || !selectedQuestion) return;
    const raw = window.localStorage.getItem(localDraftKey);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as Record<string, unknown>;
      if (
        saved.answerCandidateId !== (answer?.id ?? null) ||
        saved.rubricCandidateId !== (rubric?.id ?? null)
      )
        return;
      if (typeof saved.answerText === "string") setAnswerText(saved.answerText);
      if (typeof saved.alternativeText === "string")
        setAlternativeText(saved.alternativeText);
      if (typeof saved.rubricTitle === "string")
        setRubricTitle(saved.rubricTitle);
      if (
        saved.scoringMode === "deterministic" ||
        saved.scoringMode === "ai_suggestion" ||
        saved.scoringMode === "hybrid" ||
        saved.scoringMode === "manual_only"
      )
        setScoringMode(saved.scoringMode);
      if (typeof saved.domainJson === "string") setDomainJson(saved.domainJson);
      if (typeof saved.validationJson === "string")
        setValidationJson(saved.validationJson);
    } catch {
      window.localStorage.removeItem(localDraftKey);
    }
  }, [answer?.id, localDraftKey, rubric?.id, selectedQuestion]);

  useEffect(() => {
    if (!hasLocalEdits) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [hasLocalEdits]);

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
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(true);
    try {
      await work();
      await load();
      toast(success);
    } catch (error) {
      let text: string;
      if (error instanceof ApiError && error.status === 409) {
        if (hasLocalEdits) {
          text = `服务器内容已变化：${error.message}。本地编辑尚未保存，请复制或核对编辑内容后再决定是否刷新。`;
        } else {
          await load();
          text =
            "服务器内容已变化，系统已自动载入最新草稿；请再次执行刚才的操作。";
        }
      } else {
        text = error instanceof Error ? error.message : "操作失败";
      }
      setMessage(text);
      toast(text, "error");
    } finally {
      actionInFlight.current = false;
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
        <p>{bundleError || "还没有可核对的内容，请先整理试卷并确认题目。"}</p>
        {bundleError && <Button onClick={() => void load()}>重试</Button>}
      </Card>
    );

  return (
    <section className="space-y-4" aria-label="答案与评分标准">
      <div
        role="status"
        className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm"
      >
        <span className="text-slate-600">你现在在：</span>
        <strong>
          核对内容
          {bundleQuestion ? ` · 第 ${bundleQuestion.number} 题` : ""}
        </strong>
        {hasLocalEdits && (
          <span className="ml-2 text-emerald-700">编辑已自动保存到本机</span>
        )}
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
      <details>
        <summary className="cursor-pointer text-sm text-slate-600">
          更多操作
        </summary>
        <div className="mt-2 flex flex-wrap gap-2">
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
            处理可用答案
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
            处理可用评分标准
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => void load()}>
            重新载入
          </Button>
        </div>
      </details>
      <div className="grid gap-4 xl:grid-cols-[24rem_minmax(0,1fr)_minmax(0,1.2fr)]">
        <Card className="space-y-2 p-4">
          <div className="space-y-2">
            <h3 className="font-bold">题目</h3>
            <Button
              className="w-full"
              variant={allQuestionPackagesConfirmed ? "outline" : "primary"}
              disabled={
                busy ||
                hasLocalEdits ||
                !bundle ||
                !canConfirmAll ||
                allQuestionPackagesConfirmed
              }
              onClick={() => {
                if (!bundle) return;
                if (!allQuestionPackages && allCandidatePackages && revision) {
                  void perform(
                    () =>
                      structuredRubricApi.confirmAllCandidateQuestionPackages(
                        assignmentId,
                        {
                          expected_bundle_hash: bundle.version.bundle_hash,
                          expected_draft_revision_id: revision.id,
                          expected_draft_revision_edit_version:
                            revision.teacher_edit_version,
                          expected_source_snapshot_hash:
                            revision.source_snapshot_hash,
                          packages: allCandidatePackages,
                          explicit_confirmation: true,
                        },
                      ),
                    `已确认全部 ${allCandidatePackages.length} 道题`,
                  );
                  return;
                }
                if (!allQuestionPackages) return;
                void perform(
                  () =>
                    structuredRubricApi.confirmAllQuestionPackages(
                      assignmentId,
                      {
                        expected_bundle_hash: bundle.version.bundle_hash,
                        packages: allQuestionPackages.map((item) => ({
                          question_id: item.question_id,
                          expected_question_content_hash:
                            item.expected_question_content_hash,
                          reference_answer_version_id:
                            item.reference_answer_version_id,
                          expected_reference_answer_content_hash:
                            item.expected_reference_answer_content_hash,
                          structured_rubric_version_id:
                            item.structured_rubric_version_id,
                          expected_structured_rubric_content_hash:
                            item.expected_structured_rubric_content_hash,
                        })),
                        explicit_confirmation: true,
                      },
                    ),
                  `已确认全部 ${allQuestionPackages.length} 道题`,
                );
              }}
              title={
                hasLocalEdits
                  ? "请先保存或撤销当前编辑"
                  : !canConfirmAll
                    ? "每道题都需准备相互绑定的完整答案和评分标准"
                    : undefined
              }
            >
              {allQuestionPackagesConfirmed ? "已全部确认" : "确认全部"}
            </Button>
          </div>
          {displayQuestions.map((question) => {
            const questionNumber =
              "number" in question ? question.number : question.question_number;
            const savedQuestion = bundle?.questions.find(
              (entry) => entry.id === question.id,
            );
            const savedAnswer = savedQuestion?.answer.selected;
            const savedRubric = savedQuestion?.rubric.selected;
            const answerToConfirm =
              savedQuestion?.answer.materialized?.status === "draft"
                ? savedQuestion.answer.materialized
                : savedAnswer;
            const rubricToConfirm =
              savedQuestion?.rubric.materialized?.status === "draft"
                ? savedQuestion.rubric.materialized
                : savedRubric;
            const riskCount =
              bundle?.blockers.filter(
                (blocker) => blocker.entity_id === question.id,
              ).length ?? 0;
            const packageMatches =
              !!answerToConfirm &&
              !!rubricToConfirm &&
              rubricToConfirm.reference_answer_version_id ===
                answerToConfirm.id;
            const packageConfirmed =
              packageMatches &&
              answerToConfirm.status === "confirmed" &&
              rubricToConfirm.status === "confirmed";
            return (
              <div
                key={question.id}
                className={`flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center ${selectedQuestion === question.id ? "border-blue-600 bg-blue-50" : ""}`}
                data-testid={`question-review-card-${question.id}`}
              >
                <button
                  className="min-w-0 flex-1 p-1 text-left"
                  onClick={() => setSelectedQuestion(question.id)}
                  aria-pressed={selectedQuestion === question.id}
                >
                  <span className="font-semibold">第 {questionNumber} 题</span>
                  <span className="block text-xs">
                    {packageConfirmed
                      ? "已确认"
                      : packageMatches
                        ? "等待确认"
                        : "需要补充内容"}
                  </span>
                  {riskCount > 0 && (
                    <span className="mt-1 block text-xs text-amber-700">
                      {riskCount} 项风险待处理
                    </span>
                  )}
                </button>
                <Button
                  className="w-full shrink-0 whitespace-nowrap sm:w-auto"
                  variant={packageConfirmed ? "outline" : "primary"}
                  disabled={
                    busy ||
                    hasLocalEdits ||
                    !bundle ||
                    !packageMatches ||
                    packageConfirmed
                  }
                  onClick={() => {
                    setSelectedQuestion(question.id);
                    if (!bundle || !answerToConfirm || !rubricToConfirm) return;
                    void perform(
                      () =>
                        structuredRubricApi.confirmQuestionPackage(
                          assignmentId,
                          question.id,
                          {
                            expected_bundle_hash: bundle.version.bundle_hash,
                            expected_question_content_hash:
                              savedQuestion?.content_hash ?? "",
                            reference_answer_version_id: answerToConfirm.id,
                            expected_reference_answer_content_hash:
                              answerToConfirm.content_hash,
                            structured_rubric_version_id: rubricToConfirm.id,
                            expected_structured_rubric_content_hash:
                              rubricToConfirm.content_hash,
                            explicit_confirmation: true,
                          },
                        ),
                      `第 ${questionNumber} 题已确认`,
                    );
                  }}
                  title={
                    hasLocalEdits
                      ? "请先保存或撤销当前编辑"
                      : !packageMatches
                        ? "需先准备相互绑定的完整答案和评分标准"
                        : undefined
                  }
                >
                  {packageConfirmed ? "已确认" : "确认本题"}
                </Button>
                <details className="shrink-0 text-sm">
                  <summary className="cursor-pointer rounded-lg px-2 py-1 text-slate-600">
                    更多
                  </summary>
                  <div className="mt-2 w-full space-y-2 rounded-lg border bg-white p-3 sm:w-64">
                    <Button
                      className="w-full"
                      variant="outline"
                      disabled={
                        busy ||
                        hasLocalEdits ||
                        !revision ||
                        regeneratingQuestionId === question.id
                      }
                      onClick={async () => {
                        if (!revision) return;
                        setRegeneratingQuestionId(question.id);
                        setBusy(true);
                        setSelectedQuestion(question.id);
                        try {
                          await assignmentGenerationApi.regenerateQuestion(
                            revision.id,
                            question.id,
                            {
                              expected_source_snapshot:
                                revision.source_snapshot_hash,
                              expected_draft_revision_edit_version:
                                revision.teacher_edit_version,
                            },
                          );
                          toast(`第 ${questionNumber} 题已开始重新生成`);
                          setMessage(
                            `正在为第 ${questionNumber} 题生成新的答案与评分标准建议；旧内容和已确认内容保持不变。`,
                          );
                          window.setTimeout(() => void load(), 1500);
                          window.setTimeout(() => void load(), 4000);
                        } catch (error) {
                          setMessage(
                            error instanceof ApiError
                              ? error.message
                              : "本题暂时无法重新生成，请稍后重试。",
                          );
                        } finally {
                          setBusy(false);
                          setRegeneratingQuestionId("");
                        }
                      }}
                    >
                      {regeneratingQuestionId === question.id
                        ? "正在生成"
                        : "重新生成本题"}
                    </Button>
                    <p className="text-xs text-slate-500">
                      只生成新的答案与评分标准建议，不改题目，不覆盖已确认内容。
                    </p>
                  </div>
                </details>
              </div>
            );
          })}
        </Card>

        <Card className="space-y-3 p-4" data-testid="answer-candidate-panel">
          <h3 className="font-bold">参考答案</h3>
          {formalAnswer ? (
            <div className="space-y-3" data-testid="saved-reference-answer">
              <p className="text-sm">
                {formalAnswer.status === "confirmed" ? "已确认" : "待确认"}
              </p>
              <div className="whitespace-pre-wrap rounded-lg border bg-slate-50 p-3 text-sm">
                {formalAnswer.content}
              </div>
              {formalAnswer.status === "confirmed" && (
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
                  <p className="mt-2 text-xs text-amber-800">
                    请在左侧对应题目卡片中与评分标准一并确认。
                  </p>
                </div>
              )}
              {false &&
                bundleQuestion?.answer.history.some(
                  (entry) => entry.status === "retired",
                ) && (
                  <details className="rounded-lg border p-3 text-sm">
                    <summary className="cursor-pointer">
                      查看历史参考答案
                    </summary>
                    <ul className="mt-2 space-y-2">
                      {bundleQuestion?.answer.history
                        .filter((entry) => entry.status === "retired")
                        .map((entry) => (
                          <li
                            key={entry.id}
                            className="rounded bg-slate-50 p-2"
                          >
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
          ) : !answer ? (
            <p>尚未生成标准答案。请先确认题目，再重新生成作业内容。</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-2 text-sm">
                <span className="rounded-full bg-slate-100 px-3 py-1">
                  生成建议
                </span>
                <span>置信度 {answer.confidence.toFixed(2)}</span>
              </div>
              {answer.server_eligible === false &&
                answerEligibilityMessages.length > 0 && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
                    <p className="font-medium">此答案需要教师核对</p>
                    <ul className="mt-1 list-disc pl-5">
                      {answerEligibilityMessages.map((text) => (
                        <li key={text}>{text}</li>
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
                  onChange={(event) => {
                    setAnswerText(event.target.value);
                    saveLocalDraft({ answerText: event.target.value });
                  }}
                />
              </label>
              <label className="grid gap-1 text-sm font-medium">
                替代答案（每行一个；未验证等价性时保持 indeterminate）
                <textarea
                  className="min-h-20 rounded-lg border p-2"
                  value={alternativeText}
                  onChange={(event) => {
                    setAlternativeText(event.target.value);
                    saveLocalDraft({ alternativeText: event.target.value });
                  }}
                />
              </label>
              <details className="hidden" aria-hidden="true">
                <summary>历史生成依据</summary>
                <p className="mt-2 text-xs text-slate-500">
                  仅用于追溯当时如何生成，不代表当前代码检查已经通过。
                </p>
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
          <h3 className="font-bold">评分标准</h3>
          <RubricTemplateActions
            questionId={selectedQuestion}
            rubricId={formalRubric?.id}
            onApplied={load}
          />
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
                      {criterion.key} · {criterion.title}（{criterion.points}{" "}
                      分，
                      {criterion.required ? "必要" : "可选"}）
                    </strong>
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
              {formalRubric.status === "confirmed" ? (
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
                  <p className="mt-2 text-xs text-amber-800">
                    请在左侧对应题目卡片中与答案一并确认。
                  </p>
                </div>
              )}
              {false &&
                bundleQuestion?.rubric.history.some(
                  (entry) => entry.status === "retired",
                ) && (
                  <details className="rounded-lg border p-3 text-sm">
                    <summary className="cursor-pointer">
                      查看历史评分标准
                    </summary>
                    <ul className="mt-2 space-y-2">
                      {bundleQuestion?.rubric.history
                        .filter((entry) => entry.status === "retired")
                        .map((entry) => (
                          <li
                            key={entry.id}
                            className="rounded bg-slate-50 p-2"
                          >
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
          ) : !rubric ? (
            <p>尚未生成评分标准。请先确认题目，再重新生成作业内容。</p>
          ) : (
            <>
              <label className="grid gap-1 text-sm font-medium">
                评分标准名称
                <input
                  className="rounded-lg border p-2"
                  value={rubricTitle}
                  onChange={(event) => {
                    setRubricTitle(event.target.value);
                    saveLocalDraft({ rubricTitle: event.target.value });
                  }}
                />
              </label>
              <details className="rounded-lg border p-3 text-sm">
                <summary className="cursor-pointer font-medium">
                  更多设置
                </summary>
                <div className="mt-3 space-y-3">
                  <Select
                    label="评分方式"
                    value={scoringMode}
                    onChange={(event) => {
                      const value = event.target
                        .value as RubricDraftCandidate["scoring_mode"];
                      setScoringMode(value);
                      saveLocalDraft({ scoringMode: value });
                    }}
                  >
                    <option value="deterministic">规则评分</option>
                    <option value="ai_suggestion">智能建议</option>
                    <option value="hybrid">规则与建议结合</option>
                    <option value="manual_only">教师评分</option>
                  </Select>
                </div>
              </details>
              <p className="text-sm">
                总分：{rubric.total_points ?? "尚未设置"}
              </p>
              {rubric.server_eligible === false &&
                rubricEligibilityMessages.length > 0 && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
                    <p className="font-medium">此评分标准需要教师核对</p>
                    <ul className="mt-1 list-disc pl-5">
                      {rubricEligibilityMessages.map((text) => (
                        <li key={text}>{text}</li>
                      ))}
                    </ul>
                    <p className="mt-1 text-slate-700">
                      请补全校验规则，或核对后使用“修改后接受”。
                    </p>
                  </div>
                )}
              <div className="space-y-2" aria-label="评分项">
                {rubric.criteria.map((criterion) => (
                  <div
                    key={criterion.id}
                    className="rounded-lg border p-3 text-sm"
                  >
                    <strong>
                      {criterion.title}（{criterion.points ?? "未设置"} 分，
                      {criterion.required ? "必要" : "可选"}）
                    </strong>
                    {criterion.feedback_template && (
                      <p>{criterion.feedback_template}</p>
                    )}
                    <details className="mt-2 text-xs text-slate-600">
                      <summary className="cursor-pointer">详细信息</summary>
                      <p>类型：{criterion.criterion_type}</p>
                      <p>
                        依赖：{criterion.dependency_keys.join(", ") || "无"} ·
                        替代路径：{criterion.alternative_group ?? "无"}
                      </p>
                      <p>
                        部分分：{JSON.stringify(criterion.partial_credit_rule)}{" "}
                        · 扣分：{JSON.stringify(criterion.deduction_rule)}
                      </p>
                      <p>
                        常见错误：
                        {criterion.common_error_codes.join(", ") || "无"}
                      </p>
                    </details>
                  </div>
                ))}
              </div>
              <details className="rounded-lg border p-3 text-sm">
                <summary className="cursor-pointer font-medium">
                  高级规则
                </summary>
                <div className="mt-3 space-y-3">
                  <label className="grid gap-1 font-medium">
                    答案格式要求
                    <textarea
                      className="min-h-24 rounded-lg border p-2 font-mono text-xs"
                      value={domainJson}
                      onChange={(event) => {
                        setDomainJson(event.target.value);
                        saveLocalDraft({ domainJson: event.target.value });
                      }}
                    />
                  </label>
                  <label className="grid gap-1 font-medium">
                    自动检查规则
                    <textarea
                      className="min-h-24 rounded-lg border p-2 font-mono text-xs"
                      value={validationJson}
                      onChange={(event) => {
                        setValidationJson(event.target.value);
                        saveLocalDraft({ validationJson: event.target.value });
                      }}
                    />
                  </label>
                  <div
                    aria-label="当前代码检查"
                    className="space-y-1 rounded-lg bg-slate-50 p-3 text-sm"
                  >
                    <strong className="block">当前代码检查</strong>
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
                      <p>暂无检查结果</p>
                    )}
                  </div>
                </div>
              </details>
              <details className="hidden" aria-hidden="true">
                <summary>历史生成依据</summary>
                <p className="mt-2 text-xs text-slate-500">
                  这里是生成时保存的证据和提示，不等同于当前代码检查结果。
                </p>
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

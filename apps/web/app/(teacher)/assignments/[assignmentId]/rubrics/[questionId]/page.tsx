"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { StructuredRubricEditor } from "@/components/structured-rubric-editor";
import {
  assignmentsApi,
  structuredRubricApi,
  type ReferenceAnswerVersion,
  type StructuredRubric,
} from "@/lib/api";

export default function StructuredRubricPage() {
  const { assignmentId, questionId } = useParams<{
    assignmentId: string;
    questionId: string;
  }>();
  const [references, setReferences] = useState<ReferenceAnswerVersion[]>([]);
  const [rubrics, setRubrics] = useState<StructuredRubric[]>([]);
  const [selected, setSelected] = useState<StructuredRubric>();
  const [answer, setAnswer] = useState("");
  const [message, setMessage] = useState("");
  const [questionTitle, setQuestionTitle] = useState(questionId);
  const [diff, setDiff] = useState<string[]>([]);

  const load = useCallback(async () => {
    const [assignment, nextReferences, nextRubrics] = await Promise.all([
      assignmentsApi.get(assignmentId),
      structuredRubricApi.references(questionId),
      structuredRubricApi.list(questionId),
    ]);
    setReferences(nextReferences);
    setRubrics(nextRubrics);
    const question = assignment.paper_version?.questions.find(
      (item) => item.id === questionId,
    );
    setQuestionTitle(
      question ? `第 ${question.question_number} 题` : questionId,
    );
  }, [assignmentId, questionId]);

  useEffect(() => {
    load().catch(() => setMessage("无法加载标准答案与 Rubric"));
  }, [load]);

  const confirmedReference = useMemo(
    () => references.find((item) => item.status === "confirmed"),
    [references],
  );

  async function createReference() {
    const created = await structuredRubricApi.createReference(questionId, {
      source_type: "teacher_authored",
      raw_content: answer,
      normalized_content: answer.trim(),
      structured_content: {},
      provenance: { entered_by_teacher: true },
    });
    setReferences([created, ...references]);
    setMessage("标准答案草稿已保存；确认来源后方可用于 Rubric。");
  }

  async function createRubric() {
    if (!confirmedReference) {
      setMessage("请先确认标准答案版本。");
      return;
    }
    const created = await structuredRubricApi.create(questionId, {
      reference_answer_version_id: confirmedReference.id,
      title: `${questionTitle} Rubric`,
      total_points: "1",
      criteria: [
        {
          stable_key: "final_answer",
          title: "最终答案",
          max_points: "1",
          criterion_type: "final_answer",
          required: true,
          dependencies: [],
          validation_mode: "manual_only",
          validation_rule: {
            answer_type: "manual_only",
            domain: "rational",
          },
        },
      ],
    });
    setRubrics([created, ...rubrics]);
    setSelected(created);
  }

  return (
    <main className="space-y-6">
      <header>
        <Link
          href={`/assignments/${assignmentId}`}
          className="text-sm underline"
        >
          返回作业题目
        </Link>
        <h1 className="mt-2 text-2xl font-bold">
          {questionTitle} · 标准答案与 Rubric
        </h1>
        <p className="text-sm text-slate-600">
          标准答案、评分项、数域和等价规则均采用版本化确认；确认后的版本只读。
        </p>
      </header>

      {message && <p role="status">{message}</p>}

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-semibold">标准答案版本</h2>
        <textarea
          aria-label="标准答案草稿"
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          className="mt-3 min-h-28 w-full rounded border p-3"
        />
        <button
          type="button"
          disabled={!answer.trim()}
          onClick={() => createReference().catch(() => setMessage("保存失败"))}
          className="mt-2 rounded bg-slate-900 px-3 py-2 text-white"
        >
          保存新版本
        </button>
        <div className="mt-4 space-y-2">
          {references.map((reference) => (
            <div
              key={reference.id}
              className="flex items-center justify-between rounded border p-3"
            >
              <span>
                v{reference.version} · {reference.source_type} ·{" "}
                {reference.status}
              </span>
              {reference.status === "draft" && (
                <button
                  type="button"
                  onClick={() =>
                    structuredRubricApi
                      .confirmReference(reference.id)
                      .then(load)
                      .catch(() => setMessage("确认失败"))
                  }
                  className="rounded border px-2 py-1"
                >
                  确认来源与版本
                </button>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border bg-white p-5">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Rubric 历史版本</h2>
          <button
            type="button"
            onClick={() => createRubric().catch(() => setMessage("创建失败"))}
            className="rounded bg-slate-900 px-3 py-2 text-white"
          >
            创建 Rubric 草稿
          </button>
        </div>
        <div className="mt-3 space-y-2">
          {rubrics.map((rubric, index) => (
            <div
              key={rubric.id}
              className="flex flex-wrap items-center gap-3 rounded border p-3"
            >
              <button
                type="button"
                onClick={() => setSelected(rubric)}
                className="font-medium underline"
              >
                v{rubric.rubric_version} · {rubric.title}
              </button>
              <span>{rubric.status}</span>
              {rubric.status === "confirmed" && (
                <button
                  type="button"
                  onClick={() =>
                    structuredRubricApi
                      .derive(rubric.id)
                      .then((next) => {
                        setRubrics([next, ...rubrics]);
                        setSelected(next);
                      })
                      .catch(() => setMessage("派生失败"))
                  }
                  className="rounded border px-2 py-1"
                >
                  派生新版本
                </button>
              )}
              {index + 1 < rubrics.length && (
                <button
                  type="button"
                  onClick={() =>
                    structuredRubricApi
                      .diff(rubric.id, rubrics[index + 1].id)
                      .then((value) => setDiff(value.changed_fields))
                      .catch(() => setMessage("差异加载失败"))
                  }
                  className="rounded border px-2 py-1"
                >
                  与上一版本比较
                </button>
              )}
            </div>
          ))}
        </div>
        {diff.length > 0 && <p className="mt-3">变更字段：{diff.join("、")}</p>}
      </section>

      {selected && (
        <StructuredRubricEditor
          initial={selected}
          onCancel={() => setSelected(undefined)}
          onSave={async (rubric) => {
            const updated = await structuredRubricApi.update(rubric.id, {
              reference_answer_version_id: rubric.reference_answer_version_id,
              title: rubric.title,
              total_points: rubric.total_points,
              criteria: rubric.criteria,
            });
            setSelected(updated);
            await load();
          }}
          onConfirm={async () => {
            const validation = await structuredRubricApi.validate(selected.id);
            if (!validation.valid) {
              setMessage("Rubric 校验失败，请检查分值、依赖和验证规则。");
              return;
            }
            await structuredRubricApi.confirm(selected.id);
            setSelected(undefined);
            await load();
          }}
        />
      )}
    </main>
  );
}

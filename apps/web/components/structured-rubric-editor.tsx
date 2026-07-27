"use client";

import { useMemo, useState } from "react";
import type { StructuredCriterion, StructuredRubric } from "@/lib/api";

type Props = {
  initial: StructuredRubric;
  onSave: (rubric: StructuredRubric) => Promise<void>;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
};

const limits = {
  timeout_ms: 500,
  max_expression_length: 4096,
  max_nodes: 1000,
  max_matrix_size: 12,
  max_polynomial_degree: 20,
  max_variables: 8,
  max_expansion_terms: 500,
};

const answerTypes = [
  "exact_scalar",
  "linear_system_candidate",
  "linear_system_basis",
  "affine_solution",
  "parametric_solution_set",
  "linear_system_classification",
  "subspace_membership",
  "linear_independence",
  "subspace_basis",
  "subspace_dimension",
  "polynomial",
  "characteristic_polynomial",
  "minimal_polynomial",
  "eigenvalue_multiset",
  "eigenvector",
  "eigenspace_basis",
  "diagonalization",
] as const;

export function StructuredRubricEditor({
  initial,
  onSave,
  onConfirm,
  onCancel,
}: Props) {
  const [rubric, setRubric] = useState(initial);
  const [saving, setSaving] = useState(false);
  const readonly = rubric.status !== "draft";
  const total = useMemo(
    () =>
      rubric.criteria.reduce((sum, item) => sum + Number(item.max_points), 0),
    [rubric.criteria],
  );
  const validTotal = total === Number(rubric.total_points);

  function changeCriterion(index: number, patch: Partial<StructuredCriterion>) {
    setRubric((current) => ({
      ...current,
      criteria: current.criteria.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    }));
  }

  function move(index: number, delta: number) {
    setRubric((current) => {
      const target = index + delta;
      if (target < 0 || target >= current.criteria.length) return current;
      const criteria = [...current.criteria];
      [criteria[index], criteria[target]] = [criteria[target], criteria[index]];
      return { ...current, criteria };
    });
  }

  function updateRule(index: number, patch: Record<string, unknown>) {
    const criterion = rubric.criteria[index];
    changeCriterion(index, {
      validation_rule: { ...criterion.validation_rule, ...patch, limits },
    });
  }

  function addCriterion() {
    const key = `criterion_${rubric.criteria.length + 1}`;
    setRubric((current) => ({
      ...current,
      criteria: [
        ...current.criteria,
        {
          stable_key: key,
          title: "新评分项",
          max_points: "0",
          criterion_type: "computation",
          required: true,
          dependencies: [],
          validation_mode: "manual_only",
          validation_rule: { answer_type: "manual_only", domain: "rational" },
        },
      ],
    }));
  }

  async function save() {
    if (!validTotal || saving) return;
    setSaving(true);
    try {
      await onSave(rubric);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="结构化 Rubric 编辑器"
      className="fixed inset-0 z-50 overflow-auto bg-slate-950/45 p-8"
    >
      <section className="mx-auto max-w-6xl rounded-2xl bg-white p-6 shadow-xl">
        <header className="mb-5 flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold">
              结构化 Rubric · v{rubric.rubric_version}
            </h2>
            <p className="text-sm text-slate-600">
              确定性验证只生成建议分；教师录分与正式成绩不会被自动修改。
            </p>
          </div>
          <span className="rounded bg-slate-100 px-2 py-1 text-sm">
            {rubric.status}
          </span>
        </header>

        <label className="mb-4 block">
          标题
          <input
            aria-label="Rubric 标题"
            disabled={readonly}
            value={rubric.title}
            onChange={(event) =>
              setRubric({ ...rubric, title: event.target.value })
            }
            className="mt-1 w-full rounded border p-2"
          />
        </label>

        <div className="space-y-4">
          {rubric.criteria.map((criterion, index) => {
            const rule = criterion.validation_rule;
            return (
              <article
                key={criterion.stable_key}
                className="rounded-xl border p-4"
              >
                <div className="grid gap-3 md:grid-cols-4">
                  <input
                    aria-label={`评分项 ${index + 1} 标题`}
                    disabled={readonly}
                    value={criterion.title}
                    onChange={(event) =>
                      changeCriterion(index, { title: event.target.value })
                    }
                    className="rounded border p-2 md:col-span-2"
                  />
                  <input
                    aria-label={`评分项 ${index + 1} 分值`}
                    disabled={readonly}
                    type="number"
                    min="0"
                    value={criterion.max_points}
                    onChange={(event) =>
                      changeCriterion(index, {
                        max_points: event.target.value,
                      })
                    }
                    className="rounded border p-2"
                  />
                  <select
                    aria-label={`评分项 ${index + 1} 验证模式`}
                    disabled={readonly}
                    value={criterion.validation_mode}
                    onChange={(event) => {
                      const mode = event.target.value as
                        "deterministic" | "manual_only";
                      changeCriterion(index, {
                        validation_mode: mode,
                        validation_rule:
                          mode === "manual_only"
                            ? {
                                answer_type: "manual_only",
                                domain: "rational",
                              }
                            : {
                                answer_type: "exact_scalar",
                                domain: "rational",
                                limits,
                              },
                      });
                    }}
                    className="rounded border p-2"
                  >
                    <option value="deterministic">确定性验证</option>
                    <option value="manual_only">仅人工判断</option>
                  </select>
                </div>

                {criterion.validation_mode === "deterministic" && (
                  <div className="mt-3 grid gap-3 md:grid-cols-3">
                    <label className="text-sm">
                      答案类型
                      <select
                        aria-label={`评分项 ${index + 1} 答案类型`}
                        disabled={readonly}
                        value={String(rule.answer_type ?? "exact_scalar")}
                        onChange={(event) =>
                          updateRule(index, {
                            answer_type: event.target.value,
                          })
                        }
                        className="mt-1 w-full rounded border p-2"
                      >
                        {answerTypes.map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-sm">
                      数域
                      <select
                        aria-label={`评分项 ${index + 1} 数域`}
                        disabled={readonly}
                        value={String(rule.domain ?? "rational")}
                        onChange={(event) =>
                          updateRule(index, { domain: event.target.value })
                        }
                        className="mt-1 w-full rounded border p-2"
                      >
                        <option value="integer">整数</option>
                        <option value="rational">有理数</option>
                        <option value="real">实数</option>
                        <option value="complex">复数</option>
                      </select>
                    </label>
                    <label className="text-sm">
                      显式变量（逗号分隔）
                      <input
                        aria-label={`评分项 ${index + 1} 显式变量`}
                        disabled={readonly}
                        value={
                          Array.isArray(rule.variables)
                            ? rule.variables.join(",")
                            : ""
                        }
                        onChange={(event) =>
                          updateRule(index, {
                            variables: event.target.value
                              .split(",")
                              .map((value) => value.trim())
                              .filter(Boolean),
                          })
                        }
                        className="mt-1 w-full rounded border p-2"
                      />
                    </label>
                  </div>
                )}

                <label className="mt-3 block text-sm">
                  依赖 stable_key（逗号分隔）
                  <input
                    disabled={readonly}
                    value={criterion.dependencies.join(",")}
                    onChange={(event) =>
                      changeCriterion(index, {
                        dependencies: event.target.value
                          .split(",")
                          .map((value) => value.trim())
                          .filter(Boolean),
                      })
                    }
                    className="mt-1 w-full rounded border p-2"
                  />
                </label>

                {!readonly && (
                  <div className="mt-3 flex gap-3 text-sm">
                    <button
                      type="button"
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                      className="rounded border px-2 py-1"
                    >
                      上移
                    </button>
                    <button
                      type="button"
                      disabled={index === rubric.criteria.length - 1}
                      onClick={() => move(index, 1)}
                      className="rounded border px-2 py-1"
                    >
                      下移
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setRubric({
                          ...rubric,
                          criteria: rubric.criteria.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        })
                      }
                      className="text-red-700"
                    >
                      删除评分项
                    </button>
                  </div>
                )}
              </article>
            );
          })}
        </div>

        <div className="mt-5 flex items-center gap-3">
          {!readonly && (
            <button
              type="button"
              onClick={addCriterion}
              className="rounded border px-3 py-2"
            >
              添加评分项
            </button>
          )}
          <strong className={validTotal ? "text-emerald-700" : "text-red-700"}>
            分值合计 {total} / {rubric.total_points}
          </strong>
          {!validTotal && <span role="alert">分项总分必须等于题目满分</span>}
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded border px-3 py-2"
            >
              取消
            </button>
            {!readonly && (
              <>
                <button
                  type="button"
                  disabled={!validTotal || saving}
                  onClick={save}
                  className="rounded bg-slate-900 px-3 py-2 text-white"
                >
                  保存草稿
                </button>
                <button
                  type="button"
                  disabled={!validTotal}
                  onClick={onConfirm}
                  className="rounded bg-emerald-700 px-3 py-2 text-white"
                >
                  校验并确认
                </button>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  RubricTemplate,
  RubricTemplateCriterion,
  rubricTemplateApi,
} from "@/lib/api";
import { Button, Card, Input, PageHeader, Select } from "@/components/ui";

const blankCriterion = (index: number): RubricTemplateCriterion => ({
  stable_key: `item_${index + 1}`,
  title: `评分项 ${index + 1}`,
  description: "",
  max_points: index === 0 ? "60" : "40",
  criterion_type: "computation",
  required: true,
  dependencies: [],
  validation_mode: "ai_suggestion",
  manual_review_policy: {},
  partial_credit_policy: {},
  validation_rule: {},
  metadata: {},
});

type Draft = {
  name: string;
  subject: string;
  grade: string;
  question_type: string;
  scoring_basis: "proportional" | "fixed";
  total_points: string;
  criteria: RubricTemplateCriterion[];
};

const emptyDraft = (): Draft => ({
  name: "",
  subject: "",
  grade: "",
  question_type: "",
  scoring_basis: "proportional",
  total_points: "100",
  criteria: [blankCriterion(0), blankCriterion(1)],
});

const fromTemplate = (item: RubricTemplate): Draft => ({
  name: item.name,
  subject: item.subject ?? "",
  grade: item.grade ?? "",
  question_type: item.question_type ?? "",
  scoring_basis: item.current_version.scoring_basis,
  total_points: item.current_version.total_points,
  criteria: item.current_version.criteria,
});

export default function RubricsPage() {
  const [items, setItems] = useState<RubricTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [subject, setSubject] = useState("");
  const [grade, setGrade] = useState("");
  const [questionType, setQuestionType] = useState("");
  const [editing, setEditing] = useState<RubricTemplate | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft());
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const editSequence = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const filters = Object.fromEntries(
        Object.entries({
          search,
          subject,
          grade,
          question_type: questionType,
        }).filter(([, value]) => value),
      );
      setItems(await rubricTemplateApi.list(filters));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评分模板加载失败");
    } finally {
      setLoading(false);
    }
  }, [grade, questionType, search, subject]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    const protectLink = (event: MouseEvent) => {
      if (!dirty || !(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (!link?.getAttribute("href")?.startsWith("/")) return;
      if (!window.confirm("当前草稿尚未保存，仍要离开吗？")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", protect);
    document.addEventListener("click", protectLink, true);
    return () => {
      window.removeEventListener("beforeunload", protect);
      document.removeEventListener("click", protectLink, true);
    };
  }, [dirty]);

  const save = useCallback(async () => {
    if (!draft.name.trim()) return;
    const savingSequence = editSequence.current;
    setSaving(true);
    setError("");
    try {
      const payload = {
        ...draft,
        subject: draft.subject || null,
        grade: draft.grade || null,
        question_type: draft.question_type || null,
      };
      const saved = editing
        ? await rubricTemplateApi.update(editing.id, {
            ...payload,
            expected_content_hash: editing.current_version.content_hash,
          })
        : await rubricTemplateApi.create(payload);
      setEditing(saved);
      if (editSequence.current === savingSequence) {
        setDraft(fromTemplate(saved));
        setDirty(false);
        setNotice("草稿已保存");
      }
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }, [draft, editing, load]);

  useEffect(() => {
    if (!dirty || (editing && editing.current_version.status !== "draft"))
      return;
    const timer = window.setTimeout(() => void save(), 800);
    return () => window.clearTimeout(timer);
  }, [dirty, editing, save]);

  const facets = useMemo(
    () => ({
      subjects: [...new Set(items.map((item) => item.subject).filter(Boolean))],
      grades: [...new Set(items.map((item) => item.grade).filter(Boolean))],
      types: [
        ...new Set(items.map((item) => item.question_type).filter(Boolean)),
      ],
    }),
    [items],
  );
  const readOnly = Boolean(
    editing && editing.current_version.status !== "draft",
  );

  const mutateDraft = (patch: Partial<Draft>) => {
    editSequence.current += 1;
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
    setNotice("");
  };

  const editCriterion = (
    index: number,
    patch: Partial<RubricTemplateCriterion>,
  ) => {
    mutateDraft({
      criteria: draft.criteria.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    });
  };

  const open = async (item: RubricTemplate) => {
    if (dirty && !window.confirm("当前草稿尚未保存，仍要离开吗？"))
      return false;
    try {
      const full = await rubricTemplateApi.get(item.id);
      editSequence.current = 0;
      setEditorOpen(true);
      setEditing(full);
      setDraft(fromTemplate(full));
      setHistoryOpen(false);
      setDirty(false);
      setNotice("");
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模板加载失败");
      return false;
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="评分模板"
        description="保存可复用的评分结构；应用到题目后仍需教师确认。"
        actions={
          <Button
            onClick={() => {
              setEditorOpen(true);
              editSequence.current = 0;
              setEditing(null);
              setDraft(emptyDraft());
              setHistoryOpen(false);
              setDirty(false);
            }}
          >
            创建评分模板
          </Button>
        }
      />

      <Card className="grid gap-3 p-4 md:grid-cols-4">
        <Input
          aria-label="搜索模板"
          placeholder="搜索名称或学科"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
        >
          <option value="">全部学科</option>
          {facets.subjects.map((value) => (
            <option key={value} value={value ?? ""}>
              {value}
            </option>
          ))}
        </Select>
        <Select
          value={grade}
          onChange={(event) => setGrade(event.target.value)}
        >
          <option value="">全部年级</option>
          {facets.grades.map((value) => (
            <option key={value} value={value ?? ""}>
              {value}
            </option>
          ))}
        </Select>
        <Select
          value={questionType}
          onChange={(event) => setQuestionType(event.target.value)}
        >
          <option value="">全部题型</option>
          {facets.types.map((value) => (
            <option key={value} value={value ?? ""}>
              {value}
            </option>
          ))}
        </Select>
      </Card>

      {error && (
        <p
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
        </p>
      )}
      {!error && notice && !editorOpen && (
        <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-800">
          {notice}
        </p>
      )}
      {loading ? (
        <Card className="p-8 text-center text-sm text-slate-500">
          正在加载评分模板…
        </Card>
      ) : items.length === 0 ? (
        <Card className="p-8 text-center">
          <h2 className="font-semibold">还没有评分模板</h2>
          <p className="mt-2 text-sm text-slate-500">
            创建模板，或从题目的评分标准保存一份。
          </p>
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {items.map((item) => (
            <Card key={item.id} className="p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="font-bold">{item.name}</h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {[item.subject, item.grade, item.question_type]
                      .filter(Boolean)
                      .join(" · ") || "未分类"}
                  </p>
                </div>
                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs">
                  {item.status === "confirmed"
                    ? "已确认"
                    : item.status === "archived"
                      ? "已归档"
                      : "草稿"}
                </span>
              </div>
              <p className="mt-4 text-sm text-slate-600">
                {item.criterion_count} 个评分项 · 更新于{" "}
                {new Date(item.updated_at).toLocaleDateString("zh-CN")}
              </p>
              <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
                <Button variant="outline" onClick={() => void open(item)}>
                  查看与编辑
                </Button>
                {item.status === "confirmed" && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setEditorOpen(false);
                      setNotice(
                        "请打开草稿作业，在题目“评分标准”处选择“使用评分模板”。",
                      );
                    }}
                  >
                    使用模板
                  </Button>
                )}
                <details className="relative">
                  <summary className="cursor-pointer px-3 py-2 text-sm font-semibold">
                    更多
                  </summary>
                  <div className="absolute right-0 z-10 grid min-w-32 gap-1 rounded-lg border bg-white p-2 shadow-lg">
                    <button
                      className="p-2 text-left text-sm"
                      onClick={() =>
                        void rubricTemplateApi.duplicate(item.id).then(load)
                      }
                    >
                      复制
                    </button>
                    <button
                      className="p-2 text-left text-sm"
                      onClick={() => {
                        void open(item).then((opened) => {
                          if (opened) setHistoryOpen(true);
                        });
                      }}
                    >
                      查看历史版本
                    </button>
                    <button
                      className="p-2 text-left text-sm text-red-600"
                      onClick={() =>
                        void rubricTemplateApi.archive(item.id).then(load)
                      }
                    >
                      停用并归档
                    </button>
                  </div>
                </details>
              </div>
            </Card>
          ))}
        </div>
      )}

      {editorOpen && (
        <Card className="p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold">{editing ? "编辑模板" : "新模板"}</h2>
              <p className="text-xs text-slate-500">
                {saving ? "正在保存…" : notice || (dirty ? "有未保存修改" : "")}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                loading={saving}
                disabled={!draft.name.trim() || readOnly}
                onClick={() => void save()}
              >
                保存草稿
              </Button>
              {editing?.current_version.status === "draft" && (
                <Button
                  disabled={dirty}
                  onClick={() =>
                    void rubricTemplateApi
                      .confirm(editing.id, editing.current_version.content_hash)
                      .then((saved) => {
                        setEditing(saved);
                        setDraft(fromTemplate(saved));
                        void load();
                      })
                  }
                >
                  确认模板
                </Button>
              )}
              {editing?.current_version.status === "confirmed" && (
                <Button
                  variant="outline"
                  onClick={() =>
                    void rubricTemplateApi
                      .createVersion(editing.id)
                      .then((saved) => {
                        setEditing(saved);
                        setDraft(fromTemplate(saved));
                        void load();
                      })
                  }
                >
                  创建新版本
                </Button>
              )}
            </div>
          </div>
          {editing?.versions && editing.versions.length > 0 && (
            <details
              className="mt-4 rounded-lg border p-4"
              open={historyOpen}
              onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
            >
              <summary className="cursor-pointer font-semibold">
                历史版本（{editing.versions.length}）
              </summary>
              <div className="mt-3 space-y-2">
                {editing.versions.map((version) => (
                  <details
                    key={version.id}
                    className="rounded-lg bg-slate-50 p-3"
                  >
                    <summary className="cursor-pointer text-sm font-medium">
                      版本 {version.version} ·{" "}
                      {version.status === "confirmed"
                        ? "已确认"
                        : version.status === "archived"
                          ? "已归档"
                          : "草稿"}{" "}
                      · {version.criteria.length} 项
                    </summary>
                    <p className="mt-2 text-xs text-slate-500">
                      {version.scoring_basis === "proportional"
                        ? "按比例"
                        : "固定分值"}{" "}
                      · 合计 {version.total_points}
                    </p>
                    <ul className="mt-2 space-y-1 text-sm">
                      {version.criteria.map((criterion) => (
                        <li key={criterion.stable_key}>
                          {criterion.title}：{criterion.max_points}
                        </li>
                      ))}
                    </ul>
                  </details>
                ))}
              </div>
            </details>
          )}
          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <Input
              label="模板名称"
              value={draft.name}
              disabled={readOnly}
              onChange={(event) => mutateDraft({ name: event.target.value })}
            />
            <Select
              label="计分方式"
              value={draft.scoring_basis}
              disabled={readOnly}
              onChange={(event) =>
                mutateDraft({
                  scoring_basis: event.target.value as Draft["scoring_basis"],
                  total_points:
                    event.target.value === "proportional"
                      ? "100"
                      : draft.total_points,
                })
              }
            >
              <option value="proportional">按比例（推荐）</option>
              <option value="fixed">固定分值</option>
            </Select>
          </div>
          <details className="mt-4 rounded-lg border p-4">
            <summary className="cursor-pointer font-semibold">更多设置</summary>
            <div className="mt-4 grid gap-4 md:grid-cols-4">
              <Input
                label="学科"
                value={draft.subject}
                disabled={readOnly}
                onChange={(event) =>
                  mutateDraft({ subject: event.target.value })
                }
              />
              <Input
                label="年级"
                value={draft.grade}
                disabled={readOnly}
                onChange={(event) => mutateDraft({ grade: event.target.value })}
              />
              <Input
                label="题型"
                value={draft.question_type}
                disabled={readOnly}
                onChange={(event) =>
                  mutateDraft({ question_type: event.target.value })
                }
              />
              <Input
                label={
                  draft.scoring_basis === "proportional"
                    ? "比例合计"
                    : "模板总分"
                }
                type="number"
                value={draft.total_points}
                disabled={readOnly || draft.scoring_basis === "proportional"}
                onChange={(event) =>
                  mutateDraft({ total_points: event.target.value })
                }
              />
            </div>
          </details>
          <div className="mt-5 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">
                评分项（{draft.criteria.length}）
              </h3>
              <Button
                variant="outline"
                disabled={readOnly}
                onClick={() =>
                  mutateDraft({
                    criteria: [
                      ...draft.criteria,
                      {
                        ...blankCriterion(draft.criteria.length),
                        max_points: "0",
                      },
                    ],
                  })
                }
              >
                添加评分项
              </Button>
            </div>
            {draft.criteria.map((criterion, index) => (
              <div
                key={criterion.stable_key}
                className="grid gap-3 rounded-lg border p-4 md:grid-cols-[1fr_140px_auto]"
              >
                <Input
                  aria-label={`评分项 ${index + 1} 名称`}
                  value={criterion.title}
                  disabled={readOnly}
                  onChange={(event) =>
                    editCriterion(index, { title: event.target.value })
                  }
                />
                <Input
                  aria-label={`评分项 ${index + 1} 分值`}
                  type="number"
                  step="0.01"
                  value={criterion.max_points}
                  disabled={readOnly}
                  onChange={(event) =>
                    editCriterion(index, { max_points: event.target.value })
                  }
                />
                <Button
                  variant="ghost"
                  disabled={readOnly || draft.criteria.length === 1}
                  onClick={() =>
                    mutateDraft({
                      criteria: draft.criteria.filter(
                        (_, itemIndex) => itemIndex !== index,
                      ),
                    })
                  }
                >
                  删除
                </Button>
                <textarea
                  className="min-h-20 rounded-lg border p-3 text-sm md:col-span-3"
                  aria-label={`评分项 ${index + 1} 说明`}
                  placeholder="说明达到本评分项的要求"
                  value={criterion.description ?? ""}
                  disabled={readOnly}
                  onChange={(event) =>
                    editCriterion(index, { description: event.target.value })
                  }
                />
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

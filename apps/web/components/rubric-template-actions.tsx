"use client";

import { useEffect, useState } from "react";

import { Button, Card, Input, Select } from "@/components/ui";
import { ApiError, RubricTemplate, rubricTemplateApi } from "@/lib/api";

export function RubricTemplateActions({
  questionId,
  rubricId,
  onApplied,
}: {
  questionId: string;
  rubricId?: string | null;
  onApplied: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<RubricTemplate[]>([]);
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<Awaited<
    ReturnType<typeof rubricTemplateApi.preview>
  > | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [basis, setBasis] = useState<"proportional" | "fixed">("proportional");

  useEffect(() => {
    if (!open) return;
    setBusy(true);
    rubricTemplateApi
      .list({ status: "confirmed" })
      .then((values) => {
        setTemplates(values);
        setSelected(
          (current) => current || values[0]?.current_version.id || "",
        );
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "模板加载失败"),
      )
      .finally(() => setBusy(false));
  }, [open]);

  const showPreview = async () => {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      setPreview(await rubricTemplateApi.preview(questionId, selected));
    } catch (reason) {
      setPreview(null);
      setError(reason instanceof Error ? reason.message : "无法预览模板");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview || !selected) return;
    setBusy(true);
    setError("");
    try {
      await rubricTemplateApi.apply(questionId, {
        template_version_id: selected,
        idempotency_key: crypto.randomUUID(),
        expected_template_content_hash: preview.template_content_hash,
        expected_question_version: preview.question_version,
        reference_answer_version_id: preview.reference_answer_version_id,
        expected_reference_answer_content_hash:
          preview.reference_answer_content_hash,
      });
      setOpen(false);
      setPreview(null);
      await onApplied();
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 409
          ? `${reason.message}，请重新预览。`
          : reason instanceof Error
            ? reason.message
            : "模板应用失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!rubricId || !templateName.trim()) return;
    setBusy(true);
    setError("");
    try {
      await rubricTemplateApi.saveStructured(rubricId, {
        name: templateName.trim(),
        scoring_basis: basis,
      });
      setSaveOpen(false);
      setTemplateName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存模板失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => setOpen((value) => !value)}>
          使用评分模板
        </Button>
        {rubricId && (
          <Button
            variant="ghost"
            onClick={() => setSaveOpen((value) => !value)}
          >
            保存为模板
          </Button>
        )}
      </div>

      {open && (
        <Card className="space-y-3 p-4" data-testid="rubric-template-picker">
          <h4 className="font-semibold">选择已确认模板</h4>
          {templates.length === 0 && !busy ? (
            <p className="text-sm text-slate-500">
              暂无可用模板。请先到“评分模板”创建并确认。
            </p>
          ) : (
            <Select
              aria-label="评分模板"
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setPreview(null);
              }}
            >
              {templates.map((item) => (
                <option key={item.id} value={item.current_version.id}>
                  {item.name}（{item.criterion_count} 项）
                </option>
              ))}
            </Select>
          )}
          <Button
            variant="outline"
            loading={busy}
            disabled={!selected}
            onClick={() => void showPreview()}
          >
            预览换算
          </Button>
          {preview && (
            <div className="rounded-lg border bg-slate-50 p-3 text-sm">
              {preview.blockers.length > 0 ? (
                <div role="alert" className="text-amber-800">
                  <p className="font-medium">此模板暂不能用于本题</p>
                  <ul className="mt-1 list-disc pl-5">
                    {preview.blockers.map((blocker) => (
                      <li key={blocker.code}>{blocker.message}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <>
                  <p className="font-medium">
                    换算到本题 {preview.total_points} 分
                  </p>
                  <ul className="mt-2 space-y-1">
                    {preview.criteria.map((criterion) => (
                      <li key={criterion.stable_key}>
                        {criterion.title}：{criterion.max_points} 分
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-slate-500">
                    应用后只生成新的评分标准草稿，不会自动确认或发布。
                  </p>
                  <Button
                    className="mt-3"
                    loading={busy}
                    onClick={() => void apply()}
                  >
                    应用为本题草稿
                  </Button>
                </>
              )}
            </div>
          )}
        </Card>
      )}

      {saveOpen && rubricId && (
        <Card className="grid gap-3 p-4 md:grid-cols-[1fr_180px_auto]">
          <Input
            aria-label="模板名称"
            placeholder="模板名称"
            value={templateName}
            onChange={(event) => setTemplateName(event.target.value)}
          />
          <Select
            aria-label="模板计分方式"
            value={basis}
            onChange={(event) =>
              setBasis(event.target.value as "proportional" | "fixed")
            }
          >
            <option value="proportional">按比例（推荐）</option>
            <option value="fixed">固定分值</option>
          </Select>
          <Button
            loading={busy}
            disabled={!templateName.trim()}
            onClick={() => void save()}
          >
            保存草稿
          </Button>
          <p className="text-xs text-slate-500 md:col-span-3">
            只保存可复用的评分结构，不保存本题答案或专属证据；确认模板后才可应用。
          </p>
        </Card>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  );
}

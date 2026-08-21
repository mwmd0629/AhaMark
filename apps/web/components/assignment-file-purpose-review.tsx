"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui";
import {
  ApiError,
  assignmentGenerationApi,
  type AssignmentFileAnalysis,
} from "@/lib/api";

const labels: Record<string, string> = {
  question_paper: "题目",
  reference_answer: "答案",
  question_and_answer: "题目和答案",
};
type UploadedFile = { id: string; name: string; pageCount: number };

export function AssignmentFilePurposeReview({
  assignmentId,
  uploadedFiles,
  onDeleteFile,
  deletingFileId,
  busy = false,
}: {
  assignmentId: string;
  uploadedFiles: UploadedFile[];
  onDeleteFile: (file: UploadedFile) => void;
  deletingFileId: string;
  busy?: boolean;
}) {
  const [files, setFiles] = useState<AssignmentFileAnalysis[]>([]);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const load = useCallback(async () => {
    try {
      const revisions =
        await assignmentGenerationApi.listRevisions(assignmentId);
      if (!revisions[0]) return;
      const next = await assignmentGenerationApi.listFileAnalyses(
        revisions[0].id,
      );
      setFiles(next);
      setChoices((old) => ({
        ...old,
        ...Object.fromEntries(
          next.map((file) => [
            file.id,
            file.teacher_confirmed_role ?? file.suggested_role,
          ]),
        ),
      }));
      setError("");
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "无法识别上传文件",
      );
    }
  }, [assignmentId]);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [load]);
  if (!uploadedFiles.length) return null;
  return (
    <section
      className="mt-4 space-y-3 border-t border-emerald-200 pt-4"
      aria-label="上传文件用途确认"
    >
      <h3 className="font-semibold text-emerald-900">确认文件用途</h3>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {uploadedFiles.map((uploaded) => {
        const file = files.find((item) => item.stored_file_id === uploaded.id);
        const role = file ? (choices[file.id] ?? file.suggested_role) : "";
        const confirmed = file?.analysis_status === "confirmed";
        return (
          <div
            key={uploaded.id}
            className="rounded-lg border bg-white p-3 text-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong>{uploaded.name}</strong>
              <Button
                variant="ghost"
                className="text-red-600 hover:bg-red-50 hover:text-red-700"
                loading={deletingFileId === uploaded.id}
                disabled={busy || deletingFileId !== ""}
                onClick={() => onDeleteFile(uploaded)}
              >
                删除
              </Button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {file?.analysis_status === "suggested" ? (
                <select
                  aria-label={`${uploaded.name} 文件角色`}
                  className="rounded border p-2"
                  value={role}
                  onChange={(event) =>
                    setChoices((old) => ({
                      ...old,
                      [file.id]: event.target.value,
                    }))
                  }
                >
                  <option value="question_paper">题目</option>
                  <option value="reference_answer">答案</option>
                  <option value="question_and_answer">题目和答案</option>
                </select>
              ) : (
                <span>用途：{labels[role] ?? "等待自动识别"}</span>
              )}
              {confirmed ? (
                <span className="text-emerald-700">✓ 已确认</span>
              ) : file ? (
                <Button
                  disabled={saving || !role}
                  onClick={async () => {
                    setSaving(true);
                    try {
                      await assignmentGenerationApi.confirmFileAnalysis(
                        file.id,
                        {
                          expected_teacher_edit_version:
                            file.teacher_edit_version,
                          confirmed_role: role as
                            | "question_paper"
                            | "reference_answer"
                            | "question_and_answer",
                          confirmed_answer_source: [
                            "reference_answer",
                            "question_and_answer",
                          ].includes(role)
                            ? "teacher_provided"
                            : "not_applicable",
                        },
                      );
                      await load();
                    } catch (reason) {
                      setError(
                        reason instanceof ApiError
                          ? reason.message
                          : "保存文件用途失败",
                      );
                    } finally {
                      setSaving(false);
                    }
                  }}
                >
                  确认
                </Button>
              ) : (
                <span className="text-slate-500">等待自动识别…</span>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}

"use client";

import { useState, type FormEvent } from "react";
import { ApiError, type Student } from "@/lib/api";
import { studentAccountsApi, type StudentAccountLink } from "@/lib/student-api";
import { Button, Dialog, Input, Select } from "@/components/ui";

function temporaryPassword() {
  const alphabet =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$";
  const bytes = new Uint32Array(14);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => alphabet[value % alphabet.length]).join(
    "",
  );
}

export function StudentAccountDialog({ student }: { student: Student }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<StudentAccountLink | null>(null);
  const [submittedPassword, setSubmittedPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setResult(null);
      setSubmittedPassword("");
      setPassword("");
      setError("");
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const email = String(data.get("email") || "").trim();
    const nextPassword = mode === "new" ? password : "";
    if (!email || (mode === "new" && nextPassword.length < 8)) {
      setError("请填写邮箱；新账号的临时密码至少需要 8 位。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const linked = await studentAccountsApi.link(student.id, {
        email,
        display_name: student.name,
        temporary_password: mode === "new" ? nextPassword : undefined,
      });
      setResult(linked);
      setSubmittedPassword(mode === "new" ? nextPassword : "");
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学生账号绑定失败。",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="开通学生端账号"
      description={`为 ${student.name} 绑定可登录的学生账号。`}
      open={open}
      onOpenChange={changeOpen}
      dismissible={!saving}
      trigger={
        <Button type="button" variant="ghost">
          开通账号
        </Button>
      }
    >
      {result ? (
        <div className="space-y-4">
          <div
            role="status"
            className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800"
          >
            <strong className="block">学生账号已绑定</strong>
            <dl className="mt-3 grid gap-2">
              <div>
                <dt className="inline font-semibold">登录邮箱：</dt>
                <dd className="inline">{result.email}</dd>
              </div>
              {submittedPassword && result.created_user === true && (
                <div>
                  <dt className="inline font-semibold">临时密码：</dt>
                  <dd className="inline break-all font-mono">
                    {result.temporary_password || submittedPassword}
                  </dd>
                </div>
              )}
            </dl>
          </div>
          {submittedPassword && result.created_user === true && (
            <p className="text-sm leading-6 text-amber-800">
              临时密码只在本次窗口中展示，请通过安全方式交给学生。学生首次登录后必须先设置至少
              12 位的新密码，才能进入学习空间。
            </p>
          )}
          {result.created_user === false && (
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
              该邮箱对应的账号已经存在，本次仅完成学生档案绑定，原密码保持不变。
            </p>
          )}
          <Button type="button" onClick={() => changeOpen(false)}>
            完成
          </Button>
        </div>
      ) : (
        <form className="grid gap-4" onSubmit={submit}>
          <Select
            label="账号方式"
            value={mode}
            onChange={(event) => {
              setMode(event.target.value as "new" | "existing");
              setError("");
            }}
          >
            <option value="new">创建新学生账号</option>
            <option value="existing">绑定管理员预置学生账号</option>
          </Select>
          <Input
            name="email"
            type="email"
            label="登录邮箱"
            required
            defaultValue={student.email || ""}
            description="绑定邮箱必须与学生档案邮箱一致；如不一致，请先更新学生档案。"
          />
          {mode === "new" && (
            <div className="grid gap-2">
              <Input
                label="一次性临时密码"
                required
                minLength={8}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="new-password"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => setPassword(temporaryPassword())}
              >
                生成安全临时密码
              </Button>
            </div>
          )}
          {mode === "existing" && (
            <p className="text-xs leading-5 text-[var(--text-secondary)]">
              仅支持管理员已核验邮箱、且只具有学生角色的预置账号；不能把教师或普通账号直接改绑为学生。
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          <Button type="submit" loading={saving}>
            {mode === "new" ? "创建并绑定" : "绑定已有账号"}
          </Button>
        </form>
      )}
    </Dialog>
  );
}

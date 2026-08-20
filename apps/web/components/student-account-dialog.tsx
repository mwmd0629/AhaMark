"use client";

import { useCallback, useState, type FormEvent } from "react";
import { ApiError, type Student } from "@/lib/api";
import { studentAccountsApi, type StudentAccountLink } from "@/lib/student-api";
import { Button, Dialog, Input } from "@/components/ui";

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
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<StudentAccountLink | null>(null);
  const [submittedPassword, setSubmittedPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const changeOpen = useCallback((next: boolean) => {
    setOpen(next);
    if (!next) {
      setResult(null);
      setSubmittedPassword("");
      setPassword("");
      setError("");
    }
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const recoveryEmail = String(data.get("recovery_email") || "").trim();
    if (!student.student_number || password.length < 8) {
      setError("请确认学号；临时密码至少需要 8 位。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const linked = await studentAccountsApi.link(student.id, {
        recovery_email: recoveryEmail || null,
        display_name: student.name,
        temporary_password: password,
      });
      setResult(linked);
      setSubmittedPassword(password);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学生账号创建失败。",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="开通学生端账号"
      description={`为 ${student.name} 创建以学号登录的学生账号。`}
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
            <strong className="block">学生账号已创建并绑定</strong>
            <dl className="mt-3 grid gap-2">
              <div>
                <dt className="inline font-semibold">登录账号：</dt>
                <dd className="inline font-mono">{result.login_name}</dd>
              </div>
              <div>
                <dt className="inline font-semibold">安全邮箱：</dt>
                <dd className="inline">
                  {result.recovery_email || "未设置（学生可稍后添加）"}
                </dd>
              </div>
              <div>
                <dt className="inline font-semibold">邮箱状态：</dt>
                <dd className="inline">
                  {!result.recovery_email
                    ? "未设置"
                    : result.recovery_email_verified
                      ? "已验证"
                      : "待学生验证"}
                </dd>
              </div>
              {submittedPassword && (
                <div>
                  <dt className="inline font-semibold">临时密码：</dt>
                  <dd className="inline break-all font-mono">
                    {result.temporary_password || submittedPassword}
                  </dd>
                </div>
              )}
            </dl>
          </div>
          <p className="text-sm leading-6 text-amber-800">
            临时密码只在本次窗口中展示，请安全交给学生。学生首次登录后必须设置至少
            12
            位的新密码。安全邮箱可由学生登录后自行添加或修改；完成验证后才能使用邮件找回密码。
          </p>
          <Button type="button" onClick={() => changeOpen(false)}>
            完成
          </Button>
        </div>
      ) : (
        <form className="grid gap-4" onSubmit={submit}>
          <Input
            label="登录账号（学生学号）"
            value={student.student_number}
            readOnly
            description="账号由学生档案中的学号自动生成，不能在此修改。"
          />
          <Input
            name="recovery_email"
            type="email"
            label="安全邮箱（选填）"
            defaultValue={student.email || ""}
            autoComplete="email"
            description="可暂时留空，学生登录后仍可自行添加或修改；验证通过后用于找回密码。"
          />
          <div className="grid gap-2">
            <Input
              name="temporary_password"
              label="一次性临时密码"
              type="password"
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
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          <Button type="submit" loading={saving}>
            创建并绑定
          </Button>
        </form>
      )}
    </Dialog>
  );
}

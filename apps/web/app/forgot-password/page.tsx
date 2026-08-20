"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ApiError, authApi, type AuthChallenge } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [stage, setStage] = useState<"request" | "confirm" | "success">(
    "request",
  );
  const [challenge, setChallenge] = useState<AuthChallenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  const requestCode = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await authApi.requestPasswordReset(
        String(data.get("identifier") || "").trim(),
        String(data.get("recovery_email") || "").trim(),
      );
      setChallenge(result);
      setStage("confirm");
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "暂时无法发送验证码，请稍后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!challenge) return;
    const data = new FormData(event.currentTarget);
    const newPassword = String(data.get("new_password") || "");
    const confirmation = String(data.get("confirmation") || "");
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await authApi.confirmPasswordReset(
        challenge.challenge_id,
        String(data.get("code") || "").trim(),
        newPassword,
      );
      setSuccessMessage(result.message || "密码已重置，请使用新密码登录。");
      setStage("success");
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "密码重置失败，请检查验证码后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 p-4">
      <section className="w-full max-w-md rounded-2xl border bg-white p-7 shadow-sm">
        <div className="mb-6">
          <div className="mb-3 grid h-11 w-11 place-items-center rounded-xl bg-[var(--brand-600)] text-xl font-black text-white">
            A
          </div>
          <h1 className="text-2xl font-bold">找回密码</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            学生可使用学号和已验证的安全邮箱获取验证码并重置密码。
          </p>
        </div>

        {stage === "request" && (
          <form onSubmit={requestCode}>
            <label className="mb-4 block text-sm font-medium">
              学号
              <input
                name="identifier"
                type="text"
                autoComplete="username"
                required
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
              />
            </label>
            <label className="mb-4 block text-sm font-medium">
              安全邮箱
              <input
                name="recovery_email"
                type="email"
                autoComplete="email"
                required
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
              />
            </label>
            {error && <ErrorMessage message={error} />}
            <button
              disabled={busy}
              className="w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
            >
              {busy ? "正在发送…" : "发送验证码"}
            </button>
          </form>
        )}

        {stage === "confirm" && challenge && (
          <form onSubmit={resetPassword}>
            <div
              role="status"
              className="mb-4 rounded-lg bg-blue-50 p-3 text-sm leading-6 text-blue-800"
            >
              {challenge.message}
              <span className="block">
                验证码将在 {Math.ceil(challenge.expires_in_seconds / 60)}
                分钟后失效。
              </span>
              {challenge.development_code && (
                <strong className="mt-1 block font-mono">
                  开发环境验证码：{challenge.development_code}
                </strong>
              )}
            </div>
            <label className="mb-4 block text-sm font-medium">
              邮箱验证码
              <input
                name="code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                minLength={6}
                maxLength={12}
                required
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5 font-mono tracking-widest"
              />
            </label>
            <label className="mb-4 block text-sm font-medium">
              新密码
              <input
                name="new_password"
                type="password"
                autoComplete="new-password"
                minLength={12}
                required
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
              />
            </label>
            <label className="mb-4 block text-sm font-medium">
              再次输入新密码
              <input
                name="confirmation"
                type="password"
                autoComplete="new-password"
                minLength={12}
                required
                className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
              />
            </label>
            {error && <ErrorMessage message={error} />}
            <button
              disabled={busy}
              className="w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
            >
              {busy ? "正在重置…" : "确认重置密码"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setStage("request");
                setChallenge(null);
                setError("");
              }}
              className="mt-3 w-full rounded-lg px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50"
            >
              重新填写账号和邮箱
            </button>
          </form>
        )}

        {stage === "success" && (
          <div>
            <p
              role="status"
              className="rounded-lg bg-emerald-50 p-4 text-sm leading-6 text-emerald-800"
            >
              {successMessage}
            </p>
            <Link
              href="/login"
              className="mt-5 inline-flex min-h-10 w-full items-center justify-center rounded-lg bg-[var(--brand-600)] px-4 font-semibold text-white"
            >
              返回登录
            </Link>
          </div>
        )}

        {stage !== "success" && (
          <div className="mt-5 text-center text-sm">
            <Link
              href="/login"
              className="text-[var(--brand-700)] hover:underline"
            >
              返回登录
            </Link>
          </div>
        )}
      </section>
    </main>
  );
}

function ErrorMessage({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700"
    >
      {message}
    </p>
  );
}

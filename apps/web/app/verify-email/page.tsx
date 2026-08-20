"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError, authApi, type AuthChallenge } from "@/lib/api";
import { landingPath } from "@/lib/auth-routing";

export default function VerifyEmailPage() {
  const [currentEmail, setCurrentEmail] = useState<string | null>(null);
  const [draftEmail, setDraftEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [challenge, setChallenge] = useState<AuthChallenge | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [verified, setVerified] = useState(false);
  const [returnPath, setReturnPath] = useState("/student");

  useEffect(() => {
    let active = true;
    void authApi
      .me()
      .then((user) => {
        if (!active) return;
        setCurrentEmail(user.email);
        setDraftEmail(user.email || "");
        setVerified(user.recovery_email_verified);
        setReturnPath(landingPath(user.landing_surface) || "/student");
      })
      .catch((reason) => {
        if (!active) return;
        setError(
          reason instanceof ApiError
            ? reason.message
            : "暂时无法读取安全邮箱设置，请稍后重试。",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const saveEmail = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextEmail = draftEmail.trim() || null;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const user = await authApi.updateRecoveryEmail(
        nextEmail,
        currentPassword,
      );
      setCurrentEmail(user.email);
      setDraftEmail(user.email || "");
      setVerified(user.recovery_email_verified);
      setChallenge(null);
      setCurrentPassword("");
      setNotice(
        user.email
          ? "安全邮箱已保存，请发送验证码完成验证。"
          : "安全邮箱已清除。未设置安全邮箱时无法通过邮件找回密码。",
      );
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "安全邮箱保存失败，请稍后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  const requestCode = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setChallenge(await authApi.requestEmailVerification());
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

  const confirm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!challenge) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const user = await authApi.confirmEmailVerification(
        challenge.challenge_id,
        String(data.get("code") || "").trim(),
      );
      setReturnPath(landingPath(user.landing_surface) || "/student");
      setCurrentEmail(user.email);
      setDraftEmail(user.email || "");
      setVerified(user.recovery_email_verified);
      setChallenge(null);
      if (!user.recovery_email_verified) {
        setError("邮箱验证尚未完成，请重新获取验证码后再试。");
      } else {
        setNotice("安全邮箱验证成功，现在可以使用邮件找回密码。");
      }
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "邮箱验证失败，请检查验证码后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  const status = !currentEmail ? "未设置" : verified ? "已验证" : "未验证";
  const saveLabel =
    currentEmail && !draftEmail.trim()
      ? "清除安全邮箱"
      : currentEmail
        ? "保存安全邮箱"
        : "添加安全邮箱";

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 p-4">
      <section className="w-full max-w-lg rounded-2xl border bg-white p-7 shadow-sm">
        <div className="mb-6">
          <div className="mb-3 grid h-11 w-11 place-items-center rounded-xl bg-[var(--brand-600)] text-xl font-black text-white">
            A
          </div>
          <h1 className="text-2xl font-bold">安全邮箱设置</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            安全邮箱是选填项。验证后，忘记密码时可通过该邮箱进行二次验证并重置密码。
          </p>
        </div>

        {loading ? (
          <p role="status" className="text-sm text-slate-500">
            正在读取安全邮箱设置…
          </p>
        ) : (
          <>
            <div
              className={`mb-5 rounded-xl border p-4 text-sm ${
                verified
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-amber-200 bg-amber-50 text-amber-900"
              }`}
            >
              <p>
                <strong>当前状态：</strong>
                {status}
              </p>
              <p className="mt-1 break-all">
                <strong>安全邮箱：</strong>
                {currentEmail || "尚未设置"}
              </p>
              {verified && (
                <p className="mt-2 text-xs leading-5">
                  修改或清除邮箱后，当前验证状态会失效；新邮箱需要重新验证。
                </p>
              )}
            </div>

            <form className="grid gap-4" onSubmit={saveEmail}>
              <div>
                <label
                  htmlFor="recovery-email"
                  className="block text-sm font-medium"
                >
                  新安全邮箱（选填）
                </label>
                <input
                  id="recovery-email"
                  name="recovery_email"
                  type="email"
                  value={draftEmail}
                  onChange={(event) => setDraftEmail(event.target.value)}
                  autoComplete="email"
                  aria-describedby="recovery-email-help"
                  className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
                />
                <p
                  id="recovery-email-help"
                  className="mt-1 text-xs font-normal leading-5 text-slate-500"
                >
                  输入新邮箱可新增或更换；留空保存可清除当前安全邮箱。
                </p>
              </div>
              <div>
                <label
                  htmlFor="current-password"
                  className="block text-sm font-medium"
                >
                  当前登录密码
                </label>
                <input
                  id="current-password"
                  name="current_password"
                  type="password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  autoComplete="current-password"
                  aria-describedby="current-password-help"
                  required
                  className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
                />
                <p
                  id="current-password-help"
                  className="mt-1 text-xs font-normal leading-5 text-slate-500"
                >
                  修改邮箱属于安全操作，需要再次确认当前密码。
                </p>
              </div>
              <button
                disabled={busy}
                className="w-full rounded-lg border border-[var(--brand-600)] px-4 py-2.5 font-semibold text-[var(--brand-700)] disabled:opacity-60"
              >
                {busy ? "正在保存…" : saveLabel}
              </button>
            </form>

            {currentEmail && !verified && !challenge && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void requestCode()}
                className="mt-4 w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
              >
                {busy ? "正在发送…" : "发送邮箱验证码"}
              </button>
            )}

            {challenge && (
              <form className="mt-5" onSubmit={confirm}>
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
                <button
                  disabled={busy}
                  className="w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
                >
                  {busy ? "正在验证…" : "确认验证"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void requestCode()}
                  className="mt-3 w-full rounded-lg px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50"
                >
                  重新发送验证码
                </button>
              </form>
            )}

            {notice && (
              <p
                role="status"
                className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800"
              >
                {notice}
              </p>
            )}
            {error && <ErrorMessage message={error} />}
          </>
        )}

        <div className="mt-5 text-center text-sm">
          <Link
            href={returnPath}
            className="text-[var(--brand-700)] hover:underline"
          >
            返回学生端
          </Link>
        </div>
      </section>
    </main>
  );
}

function ErrorMessage({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700"
    >
      {message}
    </p>
  );
}

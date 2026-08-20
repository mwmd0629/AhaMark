"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, authApi } from "@/lib/api";
import { accountUnavailableMessage, landingPath } from "@/lib/auth-routing";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const currentPassword = String(data.get("current_password") || "");
    const newPassword = String(data.get("new_password") || "");
    const confirmation = String(data.get("confirmation") || "");
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const user = await authApi.changePassword(currentPassword, newPassword);
      if (
        user.landing_surface === "student" &&
        user.email &&
        !user.recovery_email_verified
      ) {
        router.replace("/verify-email");
        return;
      }
      const destination = landingPath(user.landing_surface);
      if (destination) {
        router.replace(destination);
      } else {
        await authApi.logout().catch(() => undefined);
        setError(accountUnavailableMessage);
      }
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "密码修改失败，请稍后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border bg-white p-7 shadow-sm"
      >
        <h1 className="text-2xl font-bold">首次登录，请修改密码</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          为保护学生账号，临时密码不能用于进入学习空间。新密码至少 12
          位，请勿与他人共享。
        </p>
        <label className="mt-6 block text-sm font-medium">
          当前临时密码
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            minLength={8}
            required
            className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
          />
        </label>
        <label className="mt-4 block text-sm font-medium">
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
        <label className="mt-4 block text-sm font-medium">
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
        {error && (
          <p
            role="alert"
            className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700"
          >
            {error}
          </p>
        )}
        <button
          disabled={busy}
          className="mt-6 w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
        >
          {busy ? "正在保存…" : "保存新密码并继续"}
        </button>
      </form>
    </main>
  );
}

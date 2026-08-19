"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, authApi, type AuthUser } from "@/lib/api";

function homeFor(user: AuthUser) {
  const roles = user.roles ?? [];
  if (roles.includes("admin")) return "/admin/accounts";
  if (roles.includes("student") && !roles.includes("teacher"))
    return "/student";
  return "/dashboard";
}

export default function ChangePasswordPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    authApi
      .me()
      .then(setUser)
      .catch(() => router.replace("/login"));
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("current_password") || "");
    const newPassword = String(form.get("new_password") || "");
    const confirmation = String(form.get("password_confirmation") || "");
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const nextUser = await authApi.changePassword(
        currentPassword,
        newPassword,
      );
      router.replace(homeFor(nextUser));
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "密码修改失败，请稍后重试",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 p-4">
      <form
        className="w-full max-w-md rounded-2xl border bg-white p-7 shadow-sm"
        onSubmit={submit}
      >
        <h1 className="text-2xl font-bold">修改密码</h1>
        <p className="mt-2 text-sm text-slate-600">
          修改密码是自愿操作；修改后，其他设备上的登录会话会立即失效。
        </p>
        <label className="mt-6 grid gap-1.5 text-sm font-medium">
          当前密码
          <input
            className="h-10 rounded-lg border px-3"
            name="current_password"
            type="password"
            autoComplete="current-password"
            minLength={8}
            required
          />
        </label>
        <label className="mt-4 grid gap-1.5 text-sm font-medium">
          新密码
          <input
            className="h-10 rounded-lg border px-3"
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={256}
            required
          />
          <span className="text-xs font-normal text-slate-500">
            至少 12 个字符
          </span>
        </label>
        <label className="mt-4 grid gap-1.5 text-sm font-medium">
          再次输入新密码
          <input
            className="h-10 rounded-lg border px-3"
            name="password_confirmation"
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={256}
            required
          />
        </label>
        {error && (
          <p
            className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700"
            role="alert"
          >
            {error}
          </p>
        )}
        <button
          className="mt-6 w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
          disabled={busy || !user}
        >
          {busy ? "正在修改…" : "保存新密码"}
        </button>
      </form>
    </main>
  );
}

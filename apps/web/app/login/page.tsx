"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, authApi } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const user = await authApi.login(
        String(data.get("username")),
        String(data.get("password")),
      );
      const roles = user.roles ?? [];
      router.replace(
        roles.includes("student") && !roles.includes("teacher")
          ? "/student"
          : "/dashboard",
      );
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "登录失败，请稍后重试",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 p-4">
      <form
        onSubmit={submit}
        autoComplete="off"
        data-form-type="other"
        className="w-full max-w-sm rounded-2xl border bg-white p-7 shadow-sm"
      >
        <div className="mb-6">
          <div className="mb-3 grid h-11 w-11 place-items-center rounded-xl bg-[var(--brand-600)] text-xl font-black text-white">
            A
          </div>
          <h1 className="text-2xl font-bold">登录 AhaMark</h1>
          <p className="mt-1 text-sm text-slate-500">账号由管理员统一发放</p>
        </div>
        <label className="mb-4 block text-sm font-medium">
          用户名
          <input
            name="username"
            type="text"
            autoComplete="username"
            data-1p-ignore
            data-lpignore="true"
            required
            className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
          />
        </label>
        <label className="mb-4 block text-sm font-medium">
          密码
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            data-1p-ignore
            data-lpignore="true"
            minLength={8}
            required
            className="mt-1.5 w-full rounded-lg border px-3 py-2.5"
          />
        </label>
        {error && (
          <p
            role="alert"
            className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700"
          >
            {error}
          </p>
        )}
        <button
          disabled={busy}
          className="w-full rounded-lg bg-[var(--brand-600)] px-4 py-2.5 font-semibold text-white disabled:opacity-60"
        >
          {busy ? "正在登录…" : "登录"}
        </button>
      </form>
    </main>
  );
}

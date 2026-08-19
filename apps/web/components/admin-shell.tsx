"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthUser } from "@/components/auth-gate";
import { authApi } from "@/lib/api";

export function AdminShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const user = useAuthUser();
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <Link
            href="/admin/accounts"
            className="flex items-center gap-3 font-bold"
          >
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-slate-900 text-white">
              A
            </span>
            <span>AhaMark · 管理中心</span>
          </Link>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-slate-600 sm:inline">
              {user?.display_name || user?.username}
            </span>
            <Link
              className="rounded-lg border px-3 py-2 hover:bg-slate-50"
              href="/change-password"
            >
              修改密码
            </Link>
            <button
              className="rounded-lg border px-3 py-2 hover:bg-slate-50"
              onClick={() =>
                authApi.logout().finally(() => router.replace("/login"))
              }
            >
              退出
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl p-4 sm:p-6">{children}</main>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthUser } from "@/components/auth-gate";
import { authApi } from "@/lib/api";

export function StudentShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const user = useAuthUser();
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4 sm:px-6">
          <Link href="/student" className="flex items-center gap-3 font-bold">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-[var(--brand-600)] text-white">
              A
            </span>
            <span>AhaMark · 学生端</span>
          </Link>
          <nav
            className="hidden items-center gap-1 sm:flex"
            aria-label="学生端导航"
          >
            <Link
              className="rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              href="/student"
            >
              我的成绩
            </Link>
            <Link
              className="rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              href="/student/submissions"
            >
              提交作业
            </Link>
            <Link
              className="rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              href="/student/wrong-questions"
            >
              错题与复核
            </Link>
            <Link
              className="rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              href="/student/resources"
            >
              学习资料
            </Link>
            <Link
              className="rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              href="/student/learning"
            >
              学习分析
            </Link>
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-slate-600 sm:inline">
              {user?.display_name || user?.email}
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
      <main className="mx-auto max-w-5xl p-4 sm:p-6">{children}</main>
    </div>
  );
}

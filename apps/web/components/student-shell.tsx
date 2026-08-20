"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { authApi } from "@/lib/api";
import { useStudent } from "@/components/student-auth-gate";
import {
  studentNavigation,
  studentPageTitle,
} from "@/components/student-navigation";
import { Icon } from "@/components/icons";
import { Avatar, Drawer, Dropdown, ToastProvider } from "@/components/ui";

function StudentBrand() {
  return (
    <Link href="/student" className="flex h-12 items-center gap-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--brand-600)] text-lg font-black text-white">
        A
      </span>
      <span>
        <strong className="block text-lg tracking-tight">AhaMark</strong>
        <small className="block text-[10px] text-[var(--text-secondary)]">
          学生学习空间
        </small>
      </span>
    </Link>
  );
}

function StudentNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="学生端主导航" className="grid gap-1">
      {studentNavigation.map((item) => {
        const active =
          pathname === item.href ||
          (item.href !== "/student" && pathname.startsWith(`${item.href}/`));
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={`flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition ${active ? "bg-[var(--brand-50)] text-[var(--brand-700)]" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}
          >
            <Icon name={item.icon} className="h-5 w-5 shrink-0" />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function StudentShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const student = useStudent();
  const [mobileOpen, setMobileOpen] = useState(false);
  const displayName = student?.display_name || student?.name || "同学";
  const initials = displayName.trim().slice(0, 1).toUpperCase() || "学";
  const title = studentPageTitle(pathname);

  return (
    <ToastProvider>
      <div className="min-h-screen">
        <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-[var(--border)] bg-white p-4 lg:block">
          <StudentBrand />
          <div className="mt-6">
            <StudentNav />
          </div>
          <div className="absolute inset-x-4 bottom-5 rounded-xl bg-[var(--brand-50)] p-3 text-xs leading-5 text-[var(--brand-700)]">
            AI 内容仅供学习参考；成绩变更须由教师人工复核。
          </div>
        </aside>

        <div className="lg:ml-60">
          <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-[var(--border)] bg-white/95 px-4 backdrop-blur sm:px-6">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                aria-label="打开学生端导航"
                className="grid h-10 w-10 place-items-center rounded-lg hover:bg-slate-100 lg:hidden"
              >
                <Icon name="menu" />
              </button>
              <div>
                <span className="hidden text-xs text-[var(--text-secondary)] sm:block">
                  学生端
                </span>
                <strong>{title}</strong>
              </div>
            </div>

            <Dropdown
              label={
                <span className="flex items-center gap-2 p-1">
                  <Avatar initials={initials} size="sm" />
                  <span className="hidden text-left sm:block">
                    <span className="block text-xs font-semibold">
                      {displayName}
                    </span>
                    <span className="block text-[10px] text-[var(--text-secondary)]">
                      {student?.student_number || student?.email || "学生账号"}
                    </span>
                  </span>
                </span>
              }
            >
              <Link
                href="/verify-email"
                role="menuitem"
                className="block w-full rounded-lg px-3 py-2 text-left text-sm text-amber-800 hover:bg-amber-50"
              >
                安全邮箱设置
                <span className="ml-2 text-xs font-normal text-slate-500">
                  {student?.email
                    ? student.recovery_email_verified
                      ? "已验证"
                      : "未验证"
                    : "未设置"}
                </span>
              </Link>
              <button
                type="button"
                role="menuitem"
                onClick={() =>
                  authApi.logout().finally(() => router.replace("/login"))
                }
                className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-50"
              >
                退出登录
              </button>
            </Dropdown>
          </header>
          <main className="mx-auto max-w-[1320px] p-4 pb-24 sm:p-6 lg:p-8 lg:pb-8">
            {children}
          </main>
        </div>

        <nav
          aria-label="学生端快捷导航"
          className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 border-t border-[var(--border)] bg-white px-2 pb-[max(.35rem,env(safe-area-inset-bottom))] pt-1 lg:hidden"
        >
          {studentNavigation.slice(0, 4).map((item) => {
            const active =
              pathname === item.href ||
              (item.href !== "/student" &&
                pathname.startsWith(`${item.href}/`));
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`grid min-h-14 place-items-center rounded-lg text-[10px] font-semibold ${active ? "text-[var(--brand-700)]" : "text-slate-500"}`}
              >
                <span className="grid place-items-center gap-0.5">
                  <Icon name={item.icon} className="h-5 w-5" />
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>

        <Drawer
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          title="AhaMark 学生端导航"
        >
          <StudentBrand />
          <div className="mt-6">
            <StudentNav onNavigate={() => setMobileOpen(false)} />
          </div>
        </Drawer>
      </div>
    </ToastProvider>
  );
}

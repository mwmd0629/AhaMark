"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuthUser } from "@/components/auth-gate";
import { authApi } from "@/lib/api";
import { Icon } from "./icons";
import { navigation, pageTitles } from "./navigation";
import { Avatar, Breadcrumb, Drawer, Dropdown, ToastProvider } from "./ui";

function NavItems({
  collapsed = false,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  return (
    <nav aria-label="教师端主导航" className="grid gap-1">
      {navigation.map((item) => {
        const active =
          pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            title={collapsed ? item.label : undefined}
            className={`group flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition ${active ? "bg-[var(--brand-50)] text-[var(--brand-700)]" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}
          >
            <Icon name={item.icon} className="h-5 w-5 shrink-0" />
            <span className={collapsed ? "sr-only" : "truncate"}>
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
function Brand({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <Link href="/dashboard" className="flex h-12 items-center gap-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--brand-600)] text-lg font-black text-white">
        A
      </span>
      {!collapsed && (
        <span>
          <strong className="block text-lg tracking-tight">AhaMark</strong>
          <small className="block text-[10px] text-[var(--text-secondary)]">
            AI 初批 · 教师把关
          </small>
        </span>
      )}
    </Link>
  );
}
export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const user = useAuthUser();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const title = pageTitles[pathname] ?? "教师端";
  const displayName = user?.display_name || user?.email || "教师";
  const initials = displayName.trim().slice(0, 1).toUpperCase() || "师";
  useEffect(() => {
    if (!user?.id) return;
    const key = `ahamark.notifications.unread.${user.id}`;
    const readCachedCount = () => {
      const value = Number(localStorage.getItem(key) || "0");
      setUnreadCount(Number.isFinite(value) && value > 0 ? value : 0);
    };
    const onNotifications = (event: Event) => {
      const value = Number(
        (event as CustomEvent<{ unreadCount?: number }>).detail?.unreadCount,
      );
      if (Number.isFinite(value)) setUnreadCount(Math.max(0, value));
    };
    readCachedCount();
    window.addEventListener("storage", readCachedCount);
    window.addEventListener("ahamark:notifications", onNotifications);
    return () => {
      window.removeEventListener("storage", readCachedCount);
      window.removeEventListener("ahamark:notifications", onNotifications);
    };
  }, [user?.id]);
  return (
    <ToastProvider>
      <div className="min-h-screen">
        <aside
          className={`fixed inset-y-0 left-0 z-30 hidden border-r border-[var(--border)] bg-white p-4 transition-[width] lg:block ${collapsed ? "w-[76px]" : "w-60"}`}
        >
          <Brand collapsed={collapsed} />
          <div className="mt-6">
            <NavItems collapsed={collapsed} />
          </div>
          <button
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
            className="absolute bottom-5 right-4 grid h-9 w-9 place-items-center rounded-lg border border-[var(--border)] text-slate-500 hover:bg-slate-50"
          >
            <Icon
              name="chevron"
              className={`h-4 w-4 transition ${collapsed ? "rotate-0" : "rotate-180"}`}
            />
          </button>
        </aside>
        <div
          className={`transition-[margin] ${collapsed ? "lg:ml-[76px]" : "lg:ml-60"}`}
        >
          <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-[var(--border)] bg-white/95 px-4 backdrop-blur sm:px-6">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setMobileOpen(true)}
                aria-label="打开导航"
                className="grid h-10 w-10 place-items-center rounded-lg hover:bg-slate-100 lg:hidden"
              >
                <Icon name="menu" />
              </button>
              <div className="hidden sm:block">
                <Breadcrumb items={[title]} />
              </div>
              <strong className="sm:hidden">{title}</strong>
            </div>
            <div className="flex items-center gap-1.5">
              <Link
                href="/search"
                aria-label="搜索"
                title="搜索"
                className="grid h-10 w-10 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"
              >
                <Icon name="search" />
              </Link>
              <Link
                href="/notifications"
                aria-label={
                  unreadCount ? `消息，${unreadCount} 条未读` : "消息"
                }
                title="消息"
                className="relative grid h-10 w-10 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"
              >
                <Icon name="bell" />
                {unreadCount > 0 && (
                  <span className="absolute right-0.5 top-0.5 grid min-h-4 min-w-4 place-items-center rounded-full bg-red-600 px-1 text-[10px] font-bold leading-none text-white">
                    {unreadCount > 9 ? "9+" : unreadCount}
                  </span>
                )}
              </Link>
              <Link
                href="/help"
                aria-label="使用帮助"
                title="使用帮助"
                className="hidden h-10 w-10 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 sm:grid"
              >
                <Icon name="help" />
              </Link>
              <div className="mx-2 h-6 w-px bg-[var(--border)]" />
              <Dropdown
                label={
                  <span className="flex items-center gap-2">
                    <Avatar initials={initials} size="sm" />
                    <span className="hidden text-left md:block">
                      <span className="block text-xs font-semibold">
                        {displayName}
                      </span>
                      <span className="block text-[10px] text-[var(--text-secondary)]">
                        {user?.email || "教师账号"}
                      </span>
                    </span>
                  </span>
                }
              >
                <button
                  role="menuitem"
                  onClick={() =>
                    authApi.logout().finally(() => router.replace("/login"))
                  }
                  className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-50"
                >
                  退出登录
                </button>
                <Link
                  role="menuitem"
                  href="/settings"
                  className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
                >
                  账户与设置
                </Link>
              </Dropdown>
            </div>
          </header>
          <main className="mx-auto max-w-[1480px] p-4 sm:p-6 lg:p-8">
            {children}
          </main>
        </div>
        <Drawer
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          title="AhaMark 导航"
        >
          <Brand />
          <div className="mt-6">
            <NavItems onNavigate={() => setMobileOpen(false)} />
          </div>
        </Drawer>
      </div>
    </ToastProvider>
  );
}

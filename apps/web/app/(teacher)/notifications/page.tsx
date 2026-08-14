"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuthUser } from "@/components/auth-gate";
import { Icon } from "@/components/icons";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentRecord,
  type ClassRecord,
} from "@/lib/api";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

type AccountMessage = {
  id: string;
  title: string;
  description: string;
  action: string;
  href: string;
  icon: "bell" | "classes" | "assignments" | "grading";
  tone: string;
  priority: number;
  updatedAt: string;
};

function buildMessages(
  classes: ClassRecord[],
  assignments: AssignmentRecord[],
): AccountMessage[] {
  const messages: AccountMessage[] = [];
  if (classes.length === 0) {
    messages.push({
      id: "setup-class",
      title: "先创建一个班级",
      description: "创建班级后，就能添加学生并安排作业。",
      action: "创建班级",
      href: "/classes",
      icon: "classes",
      tone: "bg-blue-100 text-blue-800",
      priority: 40,
      updatedAt: "",
    });
  }

  for (const item of classes.filter(
    (entry) => entry.active_student_count === 0,
  )) {
    messages.push({
      id: `empty-class:${item.id}:${item.updated_at}`,
      title: `“${item.name}”还没有学生`,
      description: "添加或导入学生名单后，才能为这个班级安排作业。",
      action: "添加学生",
      href: `/classes/${item.id}`,
      icon: "classes",
      tone: "bg-blue-100 text-blue-800",
      priority: 30,
      updatedAt: item.updated_at,
    });
  }

  for (const item of assignments) {
    if (item.status === "draft") {
      messages.push({
        id: `draft:${item.id}:${item.updated_at}`,
        title: `继续完善“${item.title}”`,
        description: "这份作业仍是草稿，核对题目、答案和评分标准后才能发布。",
        action: "继续编辑",
        href: `/assignments/${item.id}/edit`,
        icon: "assignments",
        tone: "bg-amber-100 text-amber-800",
        priority: 50,
        updatedAt: item.updated_at,
      });
    }
    if (item.status === "published" || item.status === "grading") {
      messages.push({
        id: `grading:${item.id}:${item.status}:${item.updated_at}`,
        title:
          item.status === "grading"
            ? `继续批改“${item.title}”`
            : `“${item.title}”可以开始收卷批改`,
        description:
          item.status === "grading"
            ? "进入批改工作台，继续处理答卷和待确认分数。"
            : "进入批改工作台，选择班级并上传答卷。",
        action: item.status === "grading" ? "继续批改" : "开始批改",
        href: `/grading?assignmentId=${item.id}`,
        icon: "grading",
        tone: "bg-emerald-100 text-emerald-800",
        priority: item.status === "grading" ? 60 : 20,
        updatedAt: item.updated_at,
      });
    }
  }

  if (classes.length > 0 && assignments.length === 0) {
    messages.push({
      id: "setup-assignment",
      title: "班级已准备好，可以创建作业",
      description: "新建作业并上传试卷，之后再核对题目和评分标准。",
      action: "创建作业",
      href: "/assignments/new",
      icon: "assignments",
      tone: "bg-violet-100 text-violet-800",
      priority: 20,
      updatedAt: "",
    });
  }

  return messages.sort(
    (left, right) =>
      right.priority - left.priority ||
      right.updatedAt.localeCompare(left.updatedAt),
  );
}

export default function NotificationsPage() {
  const user = useAuthUser();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"unread" | "all">("unread");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const storageKey = user ? `ahamark.notifications.read.${user.id}` : "";
  const unreadStorageKey = user
    ? `ahamark.notifications.unread.${user.id}`
    : "";

  const load = useCallback(async (background = false) => {
    if (!background) {
      setLoading(true);
      setError("");
    }
    try {
      const [classPage, assignmentPage] = await Promise.all([
        classesApi.list("page_size=100&status=active"),
        assignmentsApi.list("page_size=100"),
      ]);
      setClasses(classPage.items);
      setAssignments(assignmentPage.items);
    } catch (reason) {
      if (!background) {
        setError(
          reason instanceof ApiError ? reason.message : "当前账号提醒加载失败",
        );
      }
    } finally {
      if (!background) setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);
  useSmartRefresh(() => load(true), { intervalMs: 60_000 });
  useEffect(() => {
    if (!storageKey) return;
    try {
      setReadIds(new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")));
    } catch {
      setReadIds(new Set());
    }
  }, [storageKey]);

  const messages = useMemo(
    () => buildMessages(classes, assignments),
    [assignments, classes],
  );
  const unreadCount = messages.filter((item) => !readIds.has(item.id)).length;
  const visibleMessages =
    view === "unread"
      ? messages.filter((item) => !readIds.has(item.id))
      : messages;

  useEffect(() => {
    if (loading || !unreadStorageKey) return;
    localStorage.setItem(unreadStorageKey, String(unreadCount));
    window.dispatchEvent(
      new CustomEvent("ahamark:notifications", { detail: { unreadCount } }),
    );
  }, [loading, unreadCount, unreadStorageKey]);

  const saveReadIds = (next: Set<string>) => {
    setReadIds(next);
    if (storageKey) localStorage.setItem(storageKey, JSON.stringify([...next]));
  };
  const markRead = (id: string) => saveReadIds(new Set([...readIds, id]));
  const markUnread = (id: string) => {
    const next = new Set(readIds);
    next.delete(id);
    saveReadIds(next);
  };
  const markAllRead = () =>
    saveReadIds(new Set([...readIds, ...messages.map((item) => item.id)]));
  const accountName = user?.display_name || user?.email || "当前账号";

  return (
    <div className="space-y-6">
      <PageHeader
        title="消息中心"
        description={`${accountName} 当前需要继续处理的事项。提醒根据班级和作业状态自动整理。`}
        actions={
          <Button
            variant="outline"
            disabled={loading || unreadCount === 0}
            onClick={markAllRead}
          >
            全部标为已读
          </Button>
        }
      />

      <div className="flex gap-2" aria-label="消息筛选">
        <button
          type="button"
          aria-pressed={view === "unread"}
          onClick={() => setView("unread")}
          className={`rounded-full px-4 py-2 text-sm font-semibold ${
            view === "unread"
              ? "bg-[var(--brand-600)] text-white"
              : "bg-white text-slate-600"
          }`}
        >
          未读 {unreadCount}
        </button>
        <button
          type="button"
          aria-pressed={view === "all"}
          onClick={() => setView("all")}
          className={`rounded-full px-4 py-2 text-sm font-semibold ${
            view === "all"
              ? "bg-[var(--brand-600)] text-white"
              : "bg-white text-slate-600"
          }`}
        >
          全部 {messages.length}
        </button>
      </div>

      {loading ? (
        <Card className="space-y-3 p-5" aria-label="正在加载账号提醒">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </Card>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : visibleMessages.length === 0 ? (
        <EmptyState
          icon="bell"
          title={messages.length ? "没有未读提醒" : "当前没有待处理事项"}
          description={
            messages.length
              ? "需要时可切换到“全部”查看已读提醒。"
              : "班级和作业状态变化后，新的待办会显示在这里。"
          }
          action={
            messages.length ? (
              <Button variant="outline" onClick={() => setView("all")}>
                查看全部
              </Button>
            ) : undefined
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="divide-y divide-[var(--border)]">
            {visibleMessages.map((item) => {
              const read = readIds.has(item.id);
              return (
                <article
                  key={item.id}
                  className={`flex gap-4 p-5 ${read ? "bg-white" : "bg-[var(--brand-50)]/40"}`}
                >
                  <span
                    className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${item.tone}`}
                  >
                    <Icon name={item.icon} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <strong className="text-sm">{item.title}</strong>
                        <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                          {item.description}
                        </p>
                      </div>
                      {!read && (
                        <span
                          aria-label="未读"
                          className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[var(--brand-600)]"
                        />
                      )}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-4 text-sm font-semibold">
                      <Link
                        href={item.href}
                        onClick={() => markRead(item.id)}
                        className="text-[var(--brand-700)] hover:underline"
                      >
                        {item.action}
                      </Link>
                      <button
                        type="button"
                        onClick={() =>
                          read ? markUnread(item.id) : markRead(item.id)
                        }
                        className="text-slate-500 hover:text-slate-800"
                      >
                        {read ? "标为未读" : "标为已读"}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </Card>
      )}
      <p className="text-center text-xs text-[var(--text-secondary)]">
        已读状态只保存在当前浏览器，并按登录账号分别记录。
      </p>
    </div>
  );
}

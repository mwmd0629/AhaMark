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

type AccountMessage = {
  id: string;
  title: string;
  description: string;
  href: string;
  icon: "bell" | "classes" | "assignments" | "grading";
  tone: string;
};

function buildMessages(
  classes: ClassRecord[],
  assignments: AssignmentRecord[],
): AccountMessage[] {
  if (classes.length === 0) {
    return [
      {
        id: "setup-class",
        title: "创建第一个班级",
        description: "当前账号还没有班级。创建班级后即可添加学生和安排作业。",
        href: "/classes",
        icon: "classes",
        tone: "bg-blue-100 text-blue-800",
      },
    ];
  }

  const messages: AccountMessage[] = [];
  const emptyClasses = classes.filter(
    (item) => item.active_student_count === 0,
  );
  if (emptyClasses.length) {
    messages.push({
      id: `empty-classes:${emptyClasses.map((item) => item.id).join(",")}`,
      title: `${emptyClasses.length} 个班级尚未添加学生`,
      description: `包括${emptyClasses
        .slice(0, 2)
        .map((item) => `“${item.name}”`)
        .join("、")}${emptyClasses.length > 2 ? "等班级" : ""}。`,
      href: "/classes",
      icon: "classes",
      tone: "bg-blue-100 text-blue-800",
    });
  }

  const drafts = assignments.filter((item) => item.status === "draft");
  if (drafts.length) {
    messages.push({
      id: `drafts:${drafts.map((item) => `${item.id}-${item.updated_at}`).join(",")}`,
      title: `${drafts.length} 份作业草稿待完善`,
      description: "完成试卷结构和评分标准后，才能发布给班级。",
      href: "/assignments",
      icon: "assignments",
      tone: "bg-amber-100 text-amber-800",
    });
  }

  const grading = assignments.filter(
    (item) => item.status === "published" || item.status === "grading",
  );
  if (grading.length) {
    messages.push({
      id: `grading:${grading.map((item) => `${item.id}-${item.status}`).join(",")}`,
      title: `${grading.length} 份作业可进入批改流程`,
      description: "进入批改工作台上传答卷、核对识别结果并完成教师复核。",
      href: "/grading",
      icon: "grading",
      tone: "bg-emerald-100 text-emerald-800",
    });
  }

  if (assignments.length === 0) {
    messages.push({
      id: "setup-assignment",
      title: "班级已就绪，可以创建第一份作业",
      description: "选择当前账号下的班级，新建作业并上传试卷。",
      href: "/assignments/new",
      icon: "assignments",
      tone: "bg-violet-100 text-violet-800",
    });
  }
  return messages;
}

export default function NotificationsPage() {
  const user = useAuthUser();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const storageKey = user ? `ahamark.notifications.read.${user.id}` : "";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [classPage, assignmentPage] = await Promise.all([
        classesApi.list("page_size=100&status=active"),
        assignmentsApi.list("page_size=100"),
      ]);
      setClasses(classPage.items);
      setAssignments(assignmentPage.items);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "当前账号消息加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);
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
  const saveReadIds = (next: Set<string>) => {
    setReadIds(next);
    if (storageKey) localStorage.setItem(storageKey, JSON.stringify([...next]));
  };
  const markRead = (id: string) => saveReadIds(new Set([...readIds, id]));
  const markAllRead = () =>
    saveReadIds(new Set([...readIds, ...messages.map((item) => item.id)]));
  const accountName = user?.display_name || user?.email || "当前账号";

  return (
    <div className="space-y-6">
      <PageHeader
        title="消息中心"
        description={`${accountName} 的待办提醒；消息根据该账号的真实班级和作业状态生成。`}
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
      {loading ? (
        <Card className="space-y-3 p-5" aria-label="正在加载账号消息">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </Card>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : messages.length === 0 ? (
        <EmptyState
          icon="bell"
          title="当前没有待处理消息"
          description="这个账号目前没有需要提醒的班级或作业事项。"
        />
      ) : (
        <>
          <p className="text-sm text-[var(--text-secondary)]">
            {unreadCount} 条未读，共 {messages.length} 条
          </p>
          <Card className="overflow-hidden">
            <div className="divide-y divide-[var(--border)]">
              {messages.map((item) => {
                const read = readIds.has(item.id);
                return (
                  <Link
                    key={item.id}
                    href={item.href}
                    onClick={() => markRead(item.id)}
                    className={`flex gap-4 p-5 transition hover:bg-slate-50 ${
                      read ? "bg-white" : "bg-[var(--brand-50)]/40"
                    }`}
                  >
                    <span
                      className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${item.tone}`}
                    >
                      <Icon name={item.icon} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <strong className="text-sm">{item.title}</strong>
                      <span className="mt-1 block text-sm leading-6 text-[var(--text-secondary)]">
                        {item.description}
                      </span>
                    </span>
                    {!read && (
                      <span
                        aria-label="未读"
                        className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[var(--brand-600)]"
                      />
                    )}
                  </Link>
                );
              })}
            </div>
          </Card>
          <p className="text-center text-xs text-[var(--text-secondary)]">
            已读状态仅保存在本机浏览器，并按登录账号分别记录。
          </p>
        </>
      )}
    </div>
  );
}

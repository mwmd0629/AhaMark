"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuthUser } from "@/components/auth-gate";
import { Icon } from "@/components/icons";
import { Card, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentRecord,
  type ClassRecord,
} from "@/lib/api";

type SearchEntry = {
  id: string;
  title: string;
  description: string;
  href: string;
  category: "功能" | "班级" | "作业";
};

const featureEntries: SearchEntry[] = [
  {
    id: "assignments",
    title: "创建和管理作业",
    description: "新建作业、上传试卷、整理题目并设置评分标准。",
    href: "/assignments",
    category: "功能",
  },
  {
    id: "grading",
    title: "教师批改与复核",
    description: "查看识别结果、复核机器建议并完成人工评分。",
    href: "/grading",
    category: "功能",
  },
  {
    id: "classes",
    title: "班级与学生",
    description: "管理班级、学生、分组和名单导入。",
    href: "/classes",
    category: "功能",
  },
  {
    id: "analytics",
    title: "学情分析",
    description: "查看成绩分布、题目、知识点和学生趋势。",
    href: "/analytics",
    category: "功能",
  },
  {
    id: "settings",
    title: "账户与设置",
    description: "查看当前账号信息和系统连接状态。",
    href: "/settings",
    category: "功能",
  },
];

const statusLabel: Record<AssignmentRecord["status"], string> = {
  draft: "草稿",
  published: "已发布",
  grading: "批改中",
  completed: "已完成",
  archived: "已归档",
};

export default function SearchPage() {
  const user = useAuthUser();
  const [query, setQuery] = useState("");
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
        reason instanceof ApiError ? reason.message : "当前账号数据加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);

  const entries = useMemo<SearchEntry[]>(
    () => [
      ...featureEntries,
      ...classes.map((item) => ({
        id: item.id,
        title: item.name,
        description: `${[item.grade, item.subject].filter(Boolean).join(" · ") || "未填写年级与学科"} · ${item.active_student_count} 名学生`,
        href: `/classes/${item.id}`,
        category: "班级" as const,
      })),
      ...assignments.map((item) => ({
        id: item.id,
        title: item.title,
        description: `${item.classes.map((entry) => entry.name).join("、") || "未选择班级"} · ${statusLabel[item.status]}`,
        href: `/assignments/${item.id}${item.status === "draft" ? "/edit" : ""}`,
        category: "作业" as const,
      })),
    ],
    [assignments, classes],
  );
  const normalized = query.trim().toLocaleLowerCase();
  const results = useMemo(
    () =>
      normalized
        ? entries.filter((item) =>
            `${item.title} ${item.description} ${item.category}`
              .toLocaleLowerCase()
              .includes(normalized),
          )
        : entries,
    [entries, normalized],
  );
  const accountName = user?.display_name || user?.email || "当前账号";

  return (
    <div className="space-y-6">
      <PageHeader
        title="搜索"
        description={`仅搜索 ${accountName} 名下的真实班级、作业和可用功能。`}
      />
      <Card className="p-5">
        <label className="relative block">
          <span className="sr-only">输入搜索关键词</span>
          <Icon
            name="search"
            className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400"
          />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索当前账号的作业、班级或功能"
            className="h-12 w-full rounded-xl border border-[var(--border)] bg-white pl-12 pr-4 outline-none transition focus:border-[var(--brand-500)]"
          />
        </label>
        <p className="mt-3 text-xs text-[var(--text-secondary)]">
          不会显示其他教师账号的数据，也不在此处查询学生隐私信息。
        </p>
      </Card>
      {loading ? (
        <section
          aria-label="正在加载搜索数据"
          className="grid gap-3 md:grid-cols-2"
        >
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </section>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : (
        <section aria-label="搜索结果">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">
              {normalized ? `“${query.trim()}”的结果` : "当前账号可搜索内容"}
            </h2>
            <span className="text-sm text-[var(--text-secondary)]">
              {results.length} 项
            </span>
          </div>
          {results.length ? (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {results.map((item) => (
                <Link
                  key={`${item.category}-${item.id}`}
                  href={item.href}
                  className="rounded-xl border border-[var(--border)] bg-white p-5 shadow-[var(--shadow-sm)] transition hover:-translate-y-0.5 hover:border-[var(--brand-500)]"
                >
                  <span className="text-xs font-semibold text-[var(--brand-700)]">
                    {item.category}
                  </span>
                  <h3 className="mt-2 font-semibold">{item.title}</h3>
                  <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    {item.description}
                  </p>
                </Link>
              ))}
            </div>
          ) : (
            <Card className="p-10 text-center">
              <Icon name="search" className="mx-auto h-8 w-8 text-slate-300" />
              <h3 className="mt-4 font-semibold">当前账号没有匹配内容</h3>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                请更换关键词，或先创建班级和作业。
              </p>
            </Card>
          )}
        </section>
      )}
    </div>
  );
}
